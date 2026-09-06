document.addEventListener('DOMContentLoaded',()=>{
  const navToggle=document.querySelector('[data-nav-toggle]'),nav=document.querySelector('[data-nav]');
  if(navToggle&&nav) navToggle.addEventListener('click',()=>nav.classList.toggle('open'));
  document.querySelectorAll('.nav-more > button').forEach(b=>b.addEventListener('click',()=>b.parentElement.classList.toggle('open')));
  const cats=window.BUDGET_CATEGORIES||JSON.parse(document.getElementById('budget-categories')?.textContent||'[]');
  function populate(row){
    const kind=row.querySelector('.impact-kind'),sel=row.querySelector('.impact-category'); if(!kind||!sel)return;
    const selected=String(sel.dataset.selected||sel.value||''); const filtered=cats.filter(c=>!kind.value||c.kind===kind.value);
    sel.replaceChildren();
    const blank=document.createElement('option'); blank.value=''; blank.textContent='Choose category'; sel.appendChild(blank);
    filtered.forEach(c=>{const option=document.createElement('option'); option.value=String(c.id); option.textContent=c.name; option.selected=String(c.id)===selected; sel.appendChild(option);});
    sel.dataset.selected='';
  }
  document.querySelectorAll('.impact-row').forEach(row=>{populate(row); row.querySelector('.impact-kind')?.addEventListener('change',()=>populate(row));});
  document.querySelector('[data-add-impact]')?.addEventListener('click',()=>{
    const box=document.querySelector('#impact-rows'); const row=document.createElement('div'); row.className='impact-row';
    row.innerHTML='<select class="control impact-kind" name="impact_kind"><option value="">No impact</option><option value="funding">Funding</option><option value="expense">Expense</option><option value="savings">Savings</option></select><select class="control impact-category" name="impact_category"></select><input class="control" name="impact_amount" type="number" step="0.01" placeholder="Same as transaction"><input class="control" name="impact_date" type="date"><input class="control" name="impact_memo" placeholder="Memo (optional)"><button type="button" class="icon-btn danger-text" data-remove-impact>×</button>';
    box.appendChild(row); populate(row); row.querySelector('.impact-kind').addEventListener('change',()=>populate(row));
  });
  document.addEventListener('click',e=>{if(e.target.matches('[data-remove-impact]')) e.target.closest('.impact-row')?.remove();});
});

document.addEventListener('DOMContentLoaded',()=>{
  const kind=document.querySelector('#id_kind'),from=document.querySelector('#id_from_account'),to=document.querySelector('#id_to_account');
  const accounts=window.BUDGET_ACCOUNTS||JSON.parse(document.getElementById('budget-accounts')?.textContent||'[]');
  const cats=window.BUDGET_CATEGORIES||JSON.parse(document.getElementById('budget-categories')?.textContent||'[]');
  if(!kind||!from||!to||!accounts.length)return;
  const first=(types,purposes=[])=>accounts.find(a=>purposes.includes(a.purpose))||accounts.find(a=>types.includes(a.account_type));
  const setSel=(el,a)=>{if(a)el.value=String(a.id)};
  const ensureImpact=(impactKind,preferred)=>{
    let row=document.querySelector('.impact-row'); if(!row){document.querySelector('[data-add-impact]')?.click();row=document.querySelector('.impact-row')}
    if(!row)return;
    const k=row.querySelector('.impact-kind'); k.value=impactKind; k.dispatchEvent(new Event('change'));
    const c=cats.find(x=>x.kind===impactKind&&preferred.some(p=>x.name.toLowerCase().includes(p)))||cats.find(x=>x.kind===impactKind);
    if(c){const sel=row.querySelector('.impact-category'); sel.value=String(c.id)}
  };
  kind.addEventListener('change',()=>{
    const v=kind.value,external=first(['external'],['external']),main=first(['checking','cash'],['available']),saving=first(['savings'],['savings']),restricted=first(['restricted'],['restricted']),loan=first(['loan'],['liability']);
    if(v==='expense'){setSel(from,main);setSel(to,external);ensureImpact('expense',[])}
    if(v==='income'){setSel(from,external);setSel(to,main);ensureImpact('funding',['employment','salary'])}
    if(v==='savings'){setSel(from,main);setSel(to,saving);ensureImpact('savings',['saving account','emergency'])}
    if(v==='transfer'){document.querySelectorAll('.impact-row').forEach(r=>{r.querySelector('.impact-kind').value='';r.querySelector('.impact-kind').dispatchEvent(new Event('change'))})}
    if(v==='debt'){setSel(from,main);setSel(to,loan);ensureImpact('expense',['loan'])}
    if(v==='release'){
      setSel(from,restricted);setSel(to,saving||main);ensureImpact('funding',['blocked']);
      const rows=document.querySelectorAll('.impact-row'); if(rows.length<2)document.querySelector('[data-add-impact]')?.click();
      const row=document.querySelectorAll('.impact-row')[1]; if(row){const k=row.querySelector('.impact-kind');k.value='savings';k.dispatchEvent(new Event('change'));const c=cats.find(x=>x.kind==='savings'&&x.name.toLowerCase().includes('emergency'))||cats.find(x=>x.kind==='savings');if(c)row.querySelector('.impact-category').value=String(c.id)}
    }
  });
});

// Budget Manager 2.1: live monthly-plan totals + quick category creation.
document.addEventListener('DOMContentLoaded',()=>{
  const form=document.querySelector('[data-budget-form]');
  if(form){
    const currency=form.dataset.currency||'EUR';
    const format=(value)=>{
      const n=Number.isFinite(value)?value:0;
      try{return new Intl.NumberFormat(undefined,{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2}).format(n).replace(/\s+/g,' ')}catch(_){return `${n.toFixed(2)} ${currency}`}
    };
    const read=(input)=>{const n=Number.parseFloat(input.value);return Number.isFinite(n)&&n>=0?n:0};
    const recalc=()=>{
      const totals={funding:0,expense:0,savings:0};
      form.querySelectorAll('[data-budget-amount]').forEach(input=>{if(input.dataset.kind in totals)totals[input.dataset.kind]+=read(input)});
      Object.entries(totals).forEach(([kind,value])=>{const target=document.querySelector(`[data-section-total="${kind}"]`);if(target)target.textContent=format(value)});
      const remaining=totals.funding-totals.expense-totals.savings;
      const values={funding:totals.funding,expense:totals.expense,savings:totals.savings,remaining};
      Object.entries(values).forEach(([kind,value])=>{const target=document.querySelector(`[data-summary-${kind}]`);if(target)target.textContent=format(value)});
      const remainingCell=document.querySelector('.summary-cell.remaining-tone');
      const status=document.querySelector('[data-summary-status]');
      remainingCell?.classList.toggle('negative',remaining<-.004);
      if(status)status.textContent=remaining<-.004?'Overallocated':Math.abs(remaining)<.005?'Fully allocated':'Still available';
    };
    let dirty=false;
    form.querySelectorAll('[data-budget-amount]').forEach(input=>input.addEventListener('input',()=>{
      recalc();
      if(!dirty){dirty=true;const note=document.querySelector('[data-budget-dirty]');if(note){note.textContent='Unsaved budget changes';note.classList.add('dirty')}}
    }));
    recalc();
  }

  const dialog=document.querySelector('[data-category-dialog]');
  if(dialog){
    const kindInput=dialog.querySelector('[data-category-kind]');
    const title=dialog.querySelector('[data-category-title]');
    const nameInput=dialog.querySelector('[data-category-name]');
    document.querySelectorAll('[data-add-budget-category]').forEach(button=>button.addEventListener('click',()=>{
      kindInput.value=button.dataset.kind||'';
      title.textContent=`Add ${String(button.dataset.label||'category').toLowerCase()} item`;
      if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
      window.setTimeout(()=>nameInput?.focus(),20);
    }));
    dialog.querySelectorAll('[data-category-close]').forEach(button=>button.addEventListener('click',()=>dialog.close?.()));
    dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close?.()});
  }
});

// Budget Manager 2.7: non-modal floating calculator.
document.addEventListener('DOMContentLoaded',()=>{
  const panel=document.querySelector('[data-calculator-panel]');
  const input=document.querySelector('[data-calculator-expression]');
  const result=document.querySelector('[data-calculator-result]');
  const fab=document.querySelector('[data-calculator-open]');
  if(!panel||!input||!result||!fab)return;
  const tokenize=(source)=>{
    const out=[]; let i=0;
    while(i<source.length){
      const c=source[i]; if(/\s/.test(c)){i++;continue}
      if(/[+\-*/%^()]/.test(c)){out.push(c);i++;continue}
      if(/[0-9.]/.test(c)){
        let j=i+1; while(j<source.length&&/[0-9.]/.test(source[j]))j++;
        const raw=source.slice(i,j); if((raw.match(/\./g)||[]).length>1)throw new Error('Invalid number');
        const n=Number(raw); if(!Number.isFinite(n))throw new Error('Invalid number'); out.push(n); i=j; continue;
      }
      throw new Error('Unsupported character');
    }
    return out;
  };
  const calculate=(source)=>{
    const t=tokenize(source); let p=0;
    const primary=()=>{const x=t[p++];if(typeof x==='number')return x;if(x==='('){const v=expr();if(t[p++]!==')')throw new Error('Missing )');return v}throw new Error('Expected number')};
    const unary=()=>{if(t[p]==='+'){p++;return unary()}if(t[p]==='-'){p++;return -unary()}return primary()};
    const power=()=>{let v=unary();if(t[p]==='^'){p++;v=Math.pow(v,power())}return v};
    const term=()=>{let v=power();while(['*','/','%'].includes(t[p])){const op=t[p++],r=power();if((op==='/'||op==='%')&&r===0)throw new Error('Division by zero');v=op==='*'?v*r:op==='/'?v/r:v%r}return v};
    const expr=()=>{let v=term();while(['+','-'].includes(t[p])){const op=t[p++],r=term();v=op==='+'?v+r:v-r}return v};
    if(!t.length)return 0;const v=expr();if(p!==t.length)throw new Error('Invalid expression');if(!Number.isFinite(v))throw new Error('Result is not finite');return v;
  };
  const render=()=>{try{const v=calculate(input.value);result.textContent=Number.isInteger(v)?String(v):String(Number(v.toFixed(10)));result.classList.remove('error')}catch(e){result.textContent=e.message;result.classList.add('error')}};
  const clamp=()=>{
    if(panel.hidden)return;
    const r=panel.getBoundingClientRect();
    const left=Math.max(8,Math.min(window.innerWidth-r.width-8,r.left));
    const top=Math.max(8,Math.min(window.innerHeight-r.height-8,r.top));
    panel.style.left=`${left}px`;panel.style.top=`${top}px`;panel.style.right='auto';panel.style.bottom='auto';
  };
  const restorePosition=()=>{
    try{const pos=JSON.parse(sessionStorage.getItem('budgetCalculatorPosition')||'null');if(pos&&Number.isFinite(pos.left)&&Number.isFinite(pos.top)){panel.style.left=`${pos.left}px`;panel.style.top=`${pos.top}px`;panel.style.right='auto';panel.style.bottom='auto'}}catch(_){ }
    requestAnimationFrame(clamp);
  };
  const open=()=>{panel.hidden=false;fab.hidden=true;restorePosition();setTimeout(()=>input.focus(),20)};
  const close=()=>{panel.hidden=true;fab.hidden=false};
  fab.addEventListener('click',open);
  panel.querySelectorAll('[data-calculator-close]').forEach(b=>b.addEventListener('click',close));
  panel.querySelector('[data-calculator-minimize]')?.addEventListener('click',()=>panel.classList.toggle('minimized'));
  panel.querySelectorAll('[data-calc-value]').forEach(b=>b.addEventListener('click',()=>{input.value+=b.dataset.calcValue;render();input.focus()}));
  panel.querySelector('[data-calc-action="clear"]')?.addEventListener('click',()=>{input.value='';render();input.focus()});
  panel.querySelector('[data-calc-action="backspace"]')?.addEventListener('click',()=>{input.value=input.value.slice(0,-1);render();input.focus()});
  panel.querySelector('[data-calc-action="equals"]')?.addEventListener('click',()=>{try{const v=calculate(input.value);input.value=Number.isInteger(v)?String(v):String(Number(v.toFixed(10)));render()}catch(_){render()}input.focus()});
  input.addEventListener('input',render);
  input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();panel.querySelector('[data-calc-action="equals"]')?.click()}else if(e.key==='Escape'){close()}});
  const handle=panel.querySelector('[data-calculator-drag]');let drag=null;
  handle?.addEventListener('pointerdown',e=>{
    if(e.target.closest('button'))return;
    const r=panel.getBoundingClientRect();drag={dx:e.clientX-r.left,dy:e.clientY-r.top};handle.setPointerCapture?.(e.pointerId);e.preventDefault();
  });
  handle?.addEventListener('pointermove',e=>{if(!drag)return;panel.style.left=`${e.clientX-drag.dx}px`;panel.style.top=`${e.clientY-drag.dy}px`;panel.style.right='auto';panel.style.bottom='auto';clamp()});
  const stopDrag=()=>{if(!drag)return;const r=panel.getBoundingClientRect();try{sessionStorage.setItem('budgetCalculatorPosition',JSON.stringify({left:r.left,top:r.top}))}catch(_){ }drag=null};
  handle?.addEventListener('pointerup',stopDrag);handle?.addEventListener('pointercancel',stopDrag);window.addEventListener('resize',clamp);render();
});

// Report period presets own the date range; manual date edits switch to Custom.
document.addEventListener('DOMContentLoaded',()=>{
  const form=document.querySelector('[data-report-period-form]');if(!form)return;
  const preset=form.querySelector('[data-report-preset]');const dates=[...form.querySelectorAll('[data-report-date]')];
  preset?.addEventListener('change',()=>{if(preset.value!=='custom')form.requestSubmit?form.requestSubmit():form.submit()});
  dates.forEach(input=>input.addEventListener('change',()=>{if(preset)preset.value='custom'}));
});
