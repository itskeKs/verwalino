const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base='http://127.0.0.1:13000', output='/tmp/hv-b1-dunning-acceptance';
(async()=>{
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.HV_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(15000);
 const report={checks:[],pageErrors:[],responses:[]};
 page.on('pageerror',e=>report.pageErrors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)report.responses.push({status:r.status(),path:new URL(r.url()).pathname});});
 page.on('dialog',d=>d.accept());
 try{
  await page.goto(base+'/login');await page.getByLabel('E-Mail').fill('admin@alpha.de');await page.getByLabel('Passwort').fill('testpass1234');await page.getByRole('button',{name:'Anmelden',exact:true}).click();await page.waitForURL(u=>!u.pathname.includes('login'));
  const post=async(path,data,keyed=false,status=201)=>{const r=await page.request.post(base+path,{data,headers:keyed?{'Idempotency-Key':randomUUID()}: {}});assert.equal(r.status(),status,await r.text());return status===204?null:r.json();};
  const admin=(await(await page.request.get(base+'/api/users/page?eligible_for_scope=true&q=admin%40alpha.de')).json()).items[0];
  const property=await post('/api/properties',{name:'Dunning QA '+Date.now(),street:'Test 1',zip_code:'12345',city:'Test',management_type:'MIET'});
  const unit=await post('/api/units',{property_id:property.id,number:'1',usage_type:'WOHNUNG'});
  const person=await post('/api/persons',{last_name:'Dunning QA'});
  const tenant=await post('/api/tenant-profiles',{person_id:person.id});
  const lease=await post('/api/leases',{unit_id:unit.id,tenant_profile_id:tenant.id,valid_from:'2025-01-01',base_rent:'500.00',advance_payment:'100.00'});
  const scopes=[],grants=[];
  for(const [index,amount] of [[0,'100.00'],[1,'900.00']]){
   const scope=await post('/api/accounting-scopes',{property_id:property.id,scope_type:'MIET',label:'Dunning circle '+index,valid_from:'2025-01-01',valid_to:'2025-12-31',whole_property:true,units:[]},true);scopes.push(scope);
   grants.push(await post(`/api/accounting-scopes/${scope.id}/grants`,{user_id:admin.id,capabilities:['read','prepare','approve','confirm'],reason:'Synthetic dunning acceptance'},true));
   const bank=await post('/api/bank-accounts',{property_id:property.id,accounting_scope_id:scope.id,account_type:'LAUFEND',label:'Dunning bank '+index});
   const payment=await post('/api/payments',{property_id:property.id,accounting_scope_id:scope.id,bank_account_id:bank.id,tenant_profile_id:tenant.id,lease_agreement_id:lease.id,amount,direction:'IN',value_date:'2025-06-01',payment_type:'KALTMIETE',period_year:2025,period_month:6});
   await post(`/api/payments/${payment.id}/book`,{},false,200);
  }
  await page.goto(base+'/offene-posten');await page.getByLabel('Zeitraum von *').fill('2025-01-01');await page.getByLabel('Zeitraum bis *').fill('2025-12-31');
  const selector=page.getByRole('combobox',{name:'Abrechnungskreis',exact:true});await selector.locator(`option[value="${scopes[0].id}"]`).waitFor({state:'attached'});await selector.selectOption(scopes[0].id);await page.getByRole('button',{name:'Offene Posten laden',exact:true}).click();await page.getByText('7100.00 EUR offen',{exact:true}).waitFor();
  await page.getByRole('link',{name:'Mahnung erstellen',exact:true}).click();await page.getByRole('heading',{name:'Neue Mahnung anlegen'}).waitFor();await page.waitForFunction(id=>document.querySelector('select[aria-label="Abrechnungskreis"]')?.value===id,scopes[0].id);
  await page.getByLabel('Zeitraum von *').fill('2024-01-01');await page.getByRole('button',{name:'Mahnung erstellen',exact:true}).click();await page.getByText('Report period is outside accounting scope',{exact:true}).waitFor();assert.equal(await selector.inputValue(),scopes[0].id);
  await page.getByLabel('Zeitraum von *').fill('2025-01-01');await page.getByLabel('Stichtag',{exact:true}).fill('2026-01-15');await page.getByRole('button',{name:'Mahnung erstellen',exact:true}).click();await page.waitForURL(/\/mahnungen\/[^/]+$/);
  const noticeId=new URL(page.url()).pathname.split('/').pop();await page.getByRole('button',{name:'Entwurf verwerfen',exact:true}).waitFor();
  const notice=await(await page.request.get(base+'/api/dunning/'+noticeId)).json();assert.equal(notice.principal_amount,'7100.00');assert.equal(notice.accounting_scope_id,scopes[0].id);
  report.checks.push('open-items shortcut preserves visible scope; failed period validation retains it; prepared principal includes only its 100 EUR payment');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('aside').getBoundingClientRect().right<=1);report.mobile=await page.locator('main').evaluate(n=>({visible:n.clientWidth,content:n.scrollWidth}));assert.ok(report.mobile.content<=report.mobile.visible+1);await page.screenshot({path:output+'/dunning-mobile.png'});
  await page.getByRole('button',{name:'Mahnung ausgeben',exact:true}).click();await page.getByText('Ausgestellt',{exact:true}).first().waitFor();
  assert.equal((await page.request.get(base+'/api/dunning/'+noticeId+'/pdf')).status(),404);
  await post(`/api/accounting-scopes/${scopes[0].id}/grants/${grants[0].id}/revoke`,{},false,204);
  const exportGrant=await post(`/api/accounting-scopes/${scopes[0].id}/grants`,{user_id:admin.id,capabilities:['read','prepare','approve','confirm','export'],reason:'Synthetic export approval'},true);
  const pdf=await page.request.get(base+'/api/dunning/'+noticeId+'/pdf');assert.equal(pdf.status(),200);assert.ok((await pdf.body()).subarray(0,4).equals(Buffer.from('%PDF')));
  report.checks.push('issue succeeds with approval; PDF remains denied until an exact export grant exists');
  const body={tenant_profile_id:tenant.id,lease_agreement_id:lease.id,accounting_scope_id:scopes[0].id,period_start:'2025-01-01',period_end:'2025-12-31',reference_date:'2026-01-15'};
  const draft=await post('/api/dunning/tenant',body);await page.goto(base+'/mahnungen/'+draft.id);await page.getByRole('button',{name:'Entwurf verwerfen',exact:true}).click();await page.waitForURL(base+'/mahnungen');await page.getByRole('table').getByText('Verworfen',{exact:true}).first().waitFor();
  const cancelled=await(await page.request.get(base+'/api/dunning/'+draft.id)).json();assert.equal(cancelled.status,'cancelled');assert.equal(cancelled.accounting_scope_id,scopes[0].id);
  const other=await post('/api/dunning/tenant',{...body,accounting_scope_id:scopes[1].id});assert.equal(other.level,1);assert.equal(other.principal_amount,'6700.00');report.checks.push('discard preserves provenance and shows its status; another scope starts at level 1 with its own amount');
  await post(`/api/accounting-scopes/${scopes[0].id}/grants/${exportGrant.id}/revoke`,{},false,204);
  await page.goto(base+'/mahnungen/'+noticeId);await page.getByRole('alert').waitFor();assert.equal(await page.getByText('7100.00 EUR',{exact:true}).count(),0);assert.equal((await page.request.get(base+'/api/dunning/'+noticeId+'/pdf')).status(),404);report.checks.push('live revocation removes detail and PDF access with no stale financial snapshot');
  assert.deepEqual(report.pageErrors,[]);assert.ok(report.responses.every(r=>(r.status===401&&r.path==='/api/auth/session')||(r.status===409&&r.path==='/api/fristen')||(r.status===422&&r.path==='/api/dunning/tenant')||(r.status===404&&r.path.startsWith('/api/dunning/'))),JSON.stringify(report.responses));report.status='passed';
 }finally{if(report.status!=='passed')await page.screenshot({path:output+'/failed-state.png',fullPage:true});await fs.writeFile(output+'/browser-report.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1;});
