const passwordToggle=document.querySelector('#ownerPasswordToggle'),ownerPassword=document.querySelector('#ownerPassword');
if(passwordToggle&&ownerPassword){
 passwordToggle.addEventListener('pointerdown',event=>event.preventDefault());
 passwordToggle.addEventListener('click',()=>{
  const showing=ownerPassword.type==='text';
  ownerPassword.type=showing?'password':'text';
  const label=showing?'Show password':'Hide password';
  passwordToggle.setAttribute('aria-label',label);
  passwordToggle.setAttribute('title',label);
  passwordToggle.setAttribute('aria-pressed',String(!showing));
 });
}
const form=document.querySelector('#adminLogin'),error=document.querySelector('#error');
let signingIn=false;
form.onsubmit=async event=>{
 event.preventDefault();if(signingIn)return;error.textContent='';
 const login=form.elements.login,password=form.elements.password;
 for(const field of [login,password])field.removeAttribute('aria-invalid');
 for(const field of [login,password])if(!field.value.trim()){error.textContent=field===login?'Enter your login name.':'Enter your password.';field.setAttribute('aria-invalid','true');field.focus();return;}
 signingIn=true;const button=form.querySelector('button');button.disabled=true;button.textContent='Signing in…';
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
 try{const r=await fetch('/auth/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login:login.value.trim(),password:password.value}),cache:'no-store',signal:controller.signal});
  let d;try{d=await r.json();}catch{throw Error('Sign-in service unavailable. Please try again.');}
  if(!r.ok)throw Error(r.status===401?'Invalid login name or password.':'Sign-in service unavailable. Please try again.');
  password.value='';location.replace('/');
 }catch(e){error.textContent=e.name==='AbortError'?'Sign in timed out. Please try again.':e.message||'Sign-in service unavailable. Please try again.';}
 finally{clearTimeout(timer);signingIn=false;button.disabled=false;button.textContent='Sign In';}
};

const demoButton=document.querySelector('#demoOwnerLogin');
if(demoButton)demoButton.onclick=async()=>{
 error.textContent='';demoButton.disabled=true;demoButton.textContent='Opening demo…';
 try{const r=await fetch('/auth/admin/demo',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',cache:'no-store'});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.error||'Demo mode is unavailable.');location.replace('/');}
 catch(e){error.textContent=e.message||'Demo mode is unavailable.';}finally{demoButton.disabled=false;demoButton.textContent='Enter Demo Mode';}
};

fetch('/auth/admin/demo-status',{cache:'no-store'}).then(r=>r.ok?r.json():{enabled:false}).then(d=>{if(!d.enabled){document.querySelector('#demoOwnerLogin')?.setAttribute('hidden','');document.querySelector('.demoDivider')?.setAttribute('hidden','');document.querySelector('.demoHint')?.setAttribute('hidden','');}}).catch(()=>{});
