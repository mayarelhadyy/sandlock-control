"""Public representations and reporting protections; authoritative data is unchanged."""
import re

RESERVATION_FIELDS = frozenset('bookingId userId lockerId status startTime endTime createdAt updatedAt revision hourlyRate total paidAmount lateFee finalTotal paymentStatus ownerName ownerMobile zone note completedAt cancelledAt refundStatus reminderSent reminderSentAt financialVersion financialStatus lateFeeCurrent lateHourlyRate financialAsOf demoBookingValue actualCollectedRevenue'.split())

def public_reservation(record):
    return {k:v for k,v in record.items() if k in RESERVATION_FIELDS}

def safe_text(value):
    if not isinstance(value,str):return value
    # Excel and potential CSV consumers disagree about whitespace before formulas.
    probe=value.lstrip(' \t\r\n\x00\ufeff')
    if probe.startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')):
        return "'"+value
    return value

def protect_workbook(wb, export=False, canonical_ids=()):
    for ws in wb.worksheets:
        headers=[str(c.value or '').lower() for c in ws[1]]
        for row in ws.iter_rows():
            for cell in row:
                if cell.row>1:
                    header=headers[cell.column-1]
                    sensitive=header in ('pin','password','password_hash','csrf','token','cookie','secret','raw payload')
                    if ws.title=='Device Events' and header in ('value','topic'):sensitive=True
                    # Do not destroy the only PIN copy of an unimported legacy booking.
                    if sensitive and (export or ws.title!='Reservations' or str(ws.cell(cell.row,1).value) in canonical_ids):
                        cell.value='[REDACTED]';continue
                if cell.data_type=='f':cell.value="'"+str(cell.value)
                else:cell.value=safe_text(cell.value)
                if cell.hyperlink:cell.hyperlink=None
    return wb

def identifier(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value))

def bounded_text(value, maximum=500):
    return isinstance(value,str) and len(value)<=maximum and not any(ord(c)<32 and c not in '\n\t' for c in value)
