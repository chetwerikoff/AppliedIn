// Synthetic Lever topology for the production readers, never a canned PAGE result.
module.exports = ({spec,node,nodes,form,body}) => {
  const move = (e,to) => {
    e.parentElement.children.splice(e.parentElement.children.indexOf(e),1);
    e.parentElement=to; to.children.push(e);
  };
  for(const kind of spec.kinds || ['radio','checkbox']) {
    const defect=spec.defect;
    const card=node('li',{id:kind+'-card',class:'application-question custom-question'},form);
    const inner=node('div',{},card);
    const label=node('div',{class:'application-label'},inner);
    const text=node('div',{class:'text',text:'Synthetic '+kind+' question '},label);
    node('span',{class:'required',text:'*'},text);
    const field=node('div',{class:'application-field'},inner);
    const list=node('ul',{'data-qa':'synthetic-options'},field);
    const add = (type,i,name=kind+'-answer') => {
      const li=node('li',{},list);
      const caption=node('label',{text:type==='checkbox' ? 'Send synthetic updates' : ['First option','Second option'][i] || 'Foreign option'},li);
      const e=node('input',{id:kind+'-'+type+'-'+i,type,name,required:true},caption);
      e.value=String(i); e.checked=false;
      return e;
    };
    const members=[add(kind,0)];
    if(kind==='radio')members.push(add(kind,1));
    if(defect==='two-names') {
      if(kind==='radio')members[1].name='foreign-answer';
      else {add('radio',1,'foreign-one'); add('radio',2,'foreign-two');}
    }
    if(defect==='extra-text')node('input',{type:'text'},card);
    if(defect==='extra-role')node('div',{role:'checkbox'},card);
    if(defect==='two-texts')node('div',{class:'text',text:'Other question'},label);
    if(defect==='hidden-text') {text.getClientRects=()=>[];text.style.visibility='hidden';}
    if(defect==='empty-text')Object.defineProperty(text,'innerText',{get:()=>''});
    if(defect==='two-labels')node('div',{class:'application-label'},card);
    if(defect==='two-fields')node('div',{class:'application-field'},card);
    if(defect==='text-path')move(text,node('div',{},label));
    if(defect==='field-path')move(list,node('div',{},field));
    if(defect==='member-path')move(members[members.length-1].parentElement,node('div',{},list));
    if(defect==='missing-qa')list.removeAttribute('data-qa');
    if(defect==='blank-name')members[members.length-1].name='';
    if(defect==='two-checkboxes')add('checkbox',2);
    if(defect==='outside-form')move(card,body); // Form-associated inputs can live outside their owner.
    if(defect==='reassociated' || defect==='member-owner') {
      const other=node('form',{id:'other-'+kind},body); other.action=form.action;
      for(const e of defect==='member-owner' ? members.slice(-1) : members)e.form=other;
    }
    if(defect==='no-owner')for(const e of members)e.form=null;
    if(spec.wrapper) {
      const wrap=node(spec.wrapper==='fieldset' ? 'fieldset':'div',
        spec.wrapper==='fieldset' ? {} : {class:'form-group'},card);
      move(label,wrap); move(field,wrap);
      node('legend',{text:spec.emptyWrapper ? '' : 'Existing standard question'},wrap);
      if(spec.emptyWrapper) {
        // Empty prior group text still takes precedence over an otherwise valid card.
        Object.defineProperty(wrap,'innerText',{get:()=>''});
      }
    }
    if(spec.outsideCard)card.setAttribute('class','unrelated');
  }
};
