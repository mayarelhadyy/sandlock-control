"""Prototype financial assessments, never payment or settlement evidence."""
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext


def money(value):
    from reservation_service import ReservationError
    try:
        if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):raise ValueError()
        if isinstance(value,str) and not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?',value):raise ValueError()
        d=Decimal(str(value))
        if not d.is_finite() or d<0:raise ValueError()
        with localcontext() as ctx:
            ctx.prec=40
            return d.quantize(Decimal('.01'),rounding=ROUND_HALF_EVEN)
    except (ValueError,InvalidOperation,OverflowError):raise ReservationError('Invalid monetary value')


def amount_for(start,end,rate,multiplier=1):
    # Preserve backend nearest/even rounding, using decimal rather than binary money.
    seconds=Decimal(str(max(0,(end-start).total_seconds())))
    return float(money(seconds*Decimal(str(rate))*Decimal(multiplier)/Decimal(3600)))


def view(r,now):
    from reservation_service import parse_time,iso
    value=dict(r)
    if r.get('financialVersion')!=1:
        value.update(financialStatus='operator-review' if r.get('financialStatus')=='operator-review' else 'legacy-review',actualCollectedRevenue=None)
    late=r.get('lateFee',0)
    if r['status'] in ('Active','Overdue'):
        late=amount_for(parse_time(r['endTime']),now,r['hourlyRate'],3)
    value.update(lateFeeCurrent=late,lateHourlyRate=float(money(Decimal(str(r['hourlyRate']))*3)),financialAsOf=iso(now),actualCollectedRevenue=None)
    if r['status']=='Cancelled':value['demoBookingValue']=r.get('finalTotal')
    else:value['demoBookingValue']=float(money(Decimal(str(r['total']))+Decimal(str(late))))
    return value


def accept(db,r,kind,amount):
    from reservation_service import ReservationError
    if kind not in ('base','late'):raise ReservationError('Unsupported financial event')
    amount=money(amount)
    if amount==0:return None
    reference=('PAY-' if kind=='base' else 'LATE-')+r['bookingId']
    body=dict(bookingId=r['bookingId'],userId=r['userId'],kind=kind,amountMinor=int(amount*100),currency='EGP')
    encoded=json.dumps(body,sort_keys=True,separators=(',',':'))
    previous=db.execute('SELECT body FROM financial_events WHERE reference=?',(reference,)).fetchone()
    if previous:
        if previous[0]!=encoded:raise ReservationError('Financial event conflicts with accepted content',409)
        return reference
    db.execute('INSERT INTO financial_events(reference,booking_id,kind,body,created_at) VALUES(?,?,?,?,?)',(reference,r['bookingId'],kind,encoded,r['updatedAt']))
    return reference


def compatible(service,identity,body):
    from reservation_service import ReservationError
    from security import identifier
    if not identifier(body.get('bookingId')):raise ReservationError('Invalid bookingId')
    def operation(db):
        r=service.authorize(db,body.get('bookingId'),identity['user_id'])
        if not isinstance(body.get('type'),str):raise ReservationError('Invalid financial event type')
        kind={'Reservation Payment':'base','Demo Booking Value':'base','Late Fee':'late','Late Fee Assessment':'late'}.get(body.get('type'))
        if not kind:raise ReservationError('Unsupported financial event')
        if 'amount' in body:money(body['amount'])
        # Client statuses are assertions only, never instructions to settle money.
        allowed=('paid-demo','demo-only') if kind=='base' else ('due-demo','assessment-only')
        if 'status' in body and (not isinstance(body['status'],str) or body['status'] not in allowed):raise ReservationError('Unsupported financial status',409)
        reference=('PAY-' if kind=='base' else 'LATE-')+r['bookingId']
        if body.get('reference',reference)!=reference or body.get('eventId',reference)!=reference:raise ReservationError('Financial identity mismatch',409)
        row=db.execute('SELECT * FROM financial_events WHERE reference=?',(reference,)).fetchone()
        if r.get('financialVersion')!=1:raise ReservationError('Legacy financial outcome requires review',409)
        if not row:
            if kind=='late' and r['status']!='Completed':raise ReservationError('No final late fee assessment',409)
            amount=r.get('lateFee',0) if kind=='late' else r['total']
            if money(amount)!=0:raise ReservationError('Financial record requires recovery review',409)
            if 'amount' in body and Decimal(str(body['amount']))!=0:raise ReservationError('Financial content mismatch',409)
            return dict(accepted=True,created=False,eventId=None,zeroValue=True,financialStatus=r['financialStatus'])
        event=json.loads(row['body'])
        if 'amount' in body and Decimal(str(body['amount']))*100!=event['amountMinor']:raise ReservationError('Financial content mismatch',409)
        return dict(accepted=True,created=False,eventId=reference,financialStatus=r['financialStatus'],actualCollectedRevenue=None)
    return service.store.transaction(operation)


def records(store):
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM financial_events ORDER BY rowid')]


def reporting_rows(store):
    with store.connect() as db:
        rows=[dict(row) for row in db.execute('SELECT * FROM financial_events ORDER BY rowid')]
        bookings={r['bookingId']:r for r in store.rows(db)}
    result=[]
    for e in rows:
        r=bookings[e['booking_id']]
        result.append({'Timestamp':e['created_at'],'Booking ID':e['booking_id'],'User Mobile':'','Type':'Demo Booking Value' if e['kind']=='base' else 'Late Fee Assessment','Amount EGP':json.loads(e['body'])['amountMinor']/100,'Status':'demo-only' if e['kind']=='base' else 'assessment-only','Reference':e['reference'],'Financial Event':e['reference'],'Financial Outcome':r.get('financialStatus','legacy-review'),'Final Demo Value':r.get('finalTotal',r['total']),'Actual Collected Revenue':'N/A'})
    return result


def summary(rows):
    # KPI is accepted base value plus FINAL assessments, not a ticking estimate.
    total=Decimal(0);review=legacy=0
    for r in rows:
        if r.get('financialVersion')!=1:legacy+=1;continue
        if r.get('financialStatus')=='operator-review':review+=1;continue
        total+=money(r.get('finalTotal',r['total']))
    return dict(demoBookingValue=float(money(total)),actualCollectedRevenue=None,operatorReviewBookings=review,legacyReviewBookings=legacy)
