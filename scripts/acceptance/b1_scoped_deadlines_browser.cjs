const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base='http://127.0.0.1:13000', output='/tmp/hv-b1-deadlines-acceptance';
(async()=>{
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.HV_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(15000);
 const report={checks:[],pageErrors:[],responses:[]};
 page.on('pageerror',e=>report.pageErrors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)report.responses.push({status:r.status(),path:new URL(r.url()).pathname});});
 try{
  await page.goto(base+'/login'); await page.getByLabel('E-Mail').fill('admin@alpha.de');await page.getByLabel('Passwort').fill('testpass1234');await page.getByRole('button',{name:'Anmelden',exact:true}).click();await page.waitForURL(u=>!u.pathname.includes('login'));
  const post=async(path,data,keyed=false,status=201)=>{const r=await page.request.post(base+path,{data,headers:keyed?{'Idempotency-Key':randomUUID()}: {}});assert.equal(r.status(),status,await r.text());return status===204?null:r.json();};
  const admin=(await(await page.request.get(base+'/api/users/page?eligible_for_scope=true&q=admin%40alpha.de')).json()).items[0];
  const property=await post('/api/properties',{name:'Deadline QA '+Date.now(),street:'Test 1',zip_code:'12345',city:'Test',management_type:'MISCH'});
  const unit=await post('/api/units',{property_id:property.id,number:'1',usage_type:'WOHNUNG'});
  const person=await post('/api/persons',{last_name:'Statement QA'});
  const tenant=await post('/api/tenant-profiles',{person_id:person.id});
  const lease=await post('/api/leases',{unit_id:unit.id,tenant_profile_id:tenant.id,valid_from:'2025-01-01',base_rent:'500.00',advance_payment:'100.00'});
  const categories=await(await page.request.get(base+'/api/cost-categories')).json();
  const category=categories.find(row=>row.code==='GRUNDSTEUER');
  const scopes=[], grants=[];
  for(const kind of ['MIET','WEG']){
   const scope=await post('/api/accounting-scopes',{property_id:property.id,scope_type:kind,label:'Deadline '+kind,valid_from:'2025-01-01',valid_to:'2025-12-31',whole_property:true,units:[]},true);scopes.push(scope);
   grants.push(await post(`/api/accounting-scopes/${scope.id}/grants`,{user_id:admin.id,capabilities:['read','prepare','confirm'],reason:'Synthetic deadline acceptance'},true));
   await post('/api/bank-accounts',{property_id:property.id,accounting_scope_id:scope.id,account_type:'LAUFEND',label:'Deadline bank '+kind});
   const cost=await post('/api/cost-entries',{property_id:property.id,accounting_scope_id:scope.id,cost_category_id:category.id,invoice_date:'2025-06-01',service_period_from:'2025-01-01',service_period_to:'2025-12-31',gross_amount:'100.00',tenant_apportionable:'UMLAGEFAEHIG'});
   await post(`/api/cost-entries/${cost.id}/confirm`,{},false,200);
  }
  await page.goto(base+'/fristen');await page.getByRole('alert').waitFor();
  await page.getByLabel('Stichtag',{exact:true}).fill('2026-12-31');
  const selector=page.getByRole('combobox',{name:'Abrechnungskreis',exact:true});
  await selector.locator(`option[value="${scopes[0].id}"]`).waitFor({state:'attached'});await selector.selectOption(scopes[0].id);
  await page.getByText('Betriebskostenabrechnung',{exact:true}).waitFor();
  assert.equal(await page.getByText('WEG-Jahresabrechnung',{exact:true}).count(),0);
  await page.getByText('24 ueberfaellige(r) Posten (Mahnwesen pruefen).',{exact:true}).waitFor();
  await page.getByText('Aufbewahrungsübersicht nicht verfügbar; die Belegzuordnung ist noch unvollständig.',{exact:true}).waitFor();
  report.checks.push('rental scope shows its BK deadline and 24 obligation months; retention stays unknown');
  await selector.selectOption(scopes[1].id);await page.getByText('WEG-Jahresabrechnung',{exact:true}).waitFor();
  assert.equal(await page.getByText('Betriebskostenabrechnung',{exact:true}).count(),0);
  assert.equal(await page.getByText('Überfällige offene Posten',{exact:true}).count(),0);
  assert.equal(await page.getByText('Wirtschaftsplan',{exact:true}).count(),0);
  report.checks.push('WEG scope replaces rental rows and omits tenant debt and out-of-term plans');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('aside').getBoundingClientRect().right<=1);
  await selector.scrollIntoViewIfNeeded();report.mobile=await page.locator('main').evaluate(n=>({visible:n.clientWidth,content:n.scrollWidth}));assert.ok(report.mobile.content<=report.mobile.visible+1);await page.screenshot({path:output+'/deadlines-mobile.png'});
  await page.goto(base+'/');await page.getByRole('alert').waitFor();await selector.locator(`option[value="${scopes[1].id}"]`).waitFor({state:'attached'});await selector.selectOption(scopes[1].id);
  await page.getByText('Für diesen Bereich nicht ausgewiesen',{exact:true}).waitFor();assert.equal(await page.getByRole('alert').count(),0);
  report.checks.push('dashboard recovers after selecting a scope and does not invent WEG tenant-debt zero');
  await page.goto(base+'/fristen');await selector.locator(`option[value="${scopes[1].id}"]`).waitFor({state:'attached'});await selector.selectOption(scopes[1].id);await page.getByText('WEG-Jahresabrechnung',{exact:true}).waitFor();
  await post(`/api/accounting-scopes/${scopes[1].id}/grants/${grants[1].id}/revoke`,{},false,204);
  await page.getByLabel('Stichtag',{exact:true}).fill('2026-12-30');await page.getByRole('alert').filter({hasText:'not accessible'}).waitFor();assert.equal(await page.getByText('WEG-Jahresabrechnung',{exact:true}).count(),0);assert.equal(await page.getByText(/Keine überfälligen|Keine Fristen ermittelt|Alle fälligen/).count(),0);
  report.checks.push('revocation blocks the next query and removes stale rows without an all-clear');
  assert.deepEqual(report.pageErrors,[]);assert.ok(report.responses.every(r=>(r.status===401&&r.path==='/api/auth/session')||([409,404].includes(r.status)&&r.path==='/api/fristen')),JSON.stringify(report.responses));report.status='passed';
 }finally{if(report.status!=='passed')await page.screenshot({path:output+'/failed-state.png',fullPage:true});await fs.writeFile(output+'/browser-report.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1;});
