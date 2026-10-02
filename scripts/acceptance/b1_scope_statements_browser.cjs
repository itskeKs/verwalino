const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base='http://127.0.0.1:13000', output='/tmp/hv-b1-statements-acceptance';
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
  const property=await post('/api/properties',{name:'Statement QA '+Date.now(),street:'Test 1',zip_code:'12345',city:'Test',management_type:'MIET'});
  const unit=await post('/api/units',{property_id:property.id,number:'1',usage_type:'WOHNUNG'});
  const person=await post('/api/persons',{last_name:'Statement QA'});
  const tenant=await post('/api/tenant-profiles',{person_id:person.id});
  const lease=await post('/api/leases',{unit_id:unit.id,tenant_profile_id:tenant.id,valid_from:'2025-01-01',base_rent:'500.00',advance_payment:'100.00'});
  const scopes=[];const grants=[];
  for(const [index,amount] of [[0,'100.00'],[1,'900.00']]){
   const scope=await post('/api/accounting-scopes',{property_id:property.id,scope_type:'MIET',label:'Statement circle '+index,valid_from:'2025-01-01',valid_to:'2025-12-31',whole_property:true,units:[]},true);scopes.push(scope);
   grants.push(await post(`/api/accounting-scopes/${scope.id}/grants`,{user_id:admin.id,capabilities:['read','prepare','confirm'],reason:'Synthetic statement acceptance'},true));
   const bank=await post('/api/bank-accounts',{property_id:property.id,accounting_scope_id:scope.id,account_type:'LAUFEND',label:'Statement account '+index});
   const payment=await post('/api/payments',{bank_account_id:bank.id,direction:'IN',amount,value_date:'2025-06-01',payment_type:'KALTMIETE',tenant_profile_id:tenant.id,lease_agreement_id:lease.id,period_year:2025,period_month:6});
   await post(`/api/payments/${payment.id}/book`,{},false,200);
  }
  await page.goto(base+'/mietkonto');await page.getByRole('combobox',{name:/^Mietvertrag \*/}).selectOption(lease.id);
  await page.getByLabel('Zeitraum von *',{exact:true}).fill('2025-01-01');await page.getByLabel('Zeitraum bis *',{exact:true}).fill('2025-12-31');
  const selector=page.getByRole('combobox',{name:'Abrechnungskreis',exact:true});await selector.locator(`option[value="${scopes[0].id}"]`).waitFor({state:'attached'});await selector.selectOption(scopes[0].id);
  await page.getByRole('button',{name:'Abrufen',exact:true}).click();await page.locator('.info-table').getByRole('cell',{name:'100.00 EUR',exact:true}).waitFor();assert.equal(await page.getByRole('cell',{name:'1000.00 EUR',exact:true}).count(),0);report.checks.push('selected statement displays 100 EUR and excludes 900 EUR from another granted circle');
  await page.getByLabel('Zeitraum von *',{exact:true}).fill('2024-01-01');assert.equal(await page.getByRole('heading',{name:'Kontoauszug',exact:true}).count(),0);
  await page.getByRole('button',{name:'Abrufen',exact:true}).click();await page.locator('.error-text').filter({hasText:'outside accounting scope'}).waitFor();
  await page.getByLabel('Zeitraum von *',{exact:true}).fill('2025-01-01');await page.getByRole('button',{name:'Abrufen',exact:true}).click();await page.locator('.info-table').getByRole('cell',{name:'100.00 EUR',exact:true}).waitFor();report.checks.push('invalid period hides prior result; corrected period retries with the retained circle');
  await page.goto(base+'/offene-posten');await page.getByLabel('Zeitraum von *',{exact:true}).fill('2025-01-01');await page.getByLabel('Zeitraum bis *',{exact:true}).fill('2025-12-31');await selector.locator(`option[value="${scopes[0].id}"]`).waitFor({state:'attached'});await selector.selectOption(scopes[0].id);
  await page.getByRole('button',{name:'Offene Posten laden',exact:true}).click();await page.getByText('7100.00 EUR offen',{exact:true}).waitFor();report.checks.push('atomic open-items view matches the selected lease statement');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('aside').getBoundingClientRect().right<=1);await selector.scrollIntoViewIfNeeded();report.mobile=await page.locator('main').evaluate(n=>({visible:n.clientWidth,content:n.scrollWidth}));assert.ok(report.mobile.content<=report.mobile.visible+1);await page.screenshot({path:output+'/statements-mobile.png'});
  await post(`/api/accounting-scopes/${scopes[0].id}/grants/${grants[0].id}/revoke`,{},false,204);
  await page.getByRole('button',{name:'Offene Posten laden',exact:true}).click();await page.getByRole('alert').filter({hasText:'not accessible'}).waitFor();assert.equal(await page.getByText('7100.00 EUR offen',{exact:true}).count(),0);assert.equal(await page.getByText(/Keine offenen Posten/).count(),0);report.checks.push('revocation blocks retry and hides previous debt without false empty success');
  await page.goto(base+'/');await page.getByText('Fristenübersicht nicht verfügbar. Der Handlungsbedarf kann derzeit nicht ermittelt werden.').waitFor();assert.equal(await page.getByText(/alles im grünen Bereich|Alles erledigt|keine offenen Posten/).count(),0);
  await page.goto(base+'/fristen');await page.getByRole('alert').waitFor();assert.equal(await page.getByText(/Keine überfälligen|Keine Fristen ermittelt|Alle fälligen/).count(),0);report.checks.push('blocked dashboard and deadline monitor render unavailable without all-clear claims');
  assert.deepEqual(report.pageErrors,[]);assert.ok(report.responses.every(r=>(r.status===401&&r.path==='/api/auth/session')||(r.status===409&&r.path==='/api/fristen')||(r.status===422&&r.path.startsWith('/api/payments/mietkonto/'))||(r.status===404&&r.path==='/api/payments/open-items')),JSON.stringify(report.responses));report.status='passed';
 }finally{if(report.status!=='passed')await page.screenshot({path:output+'/failed-state.png',fullPage:true});await fs.writeFile(output+'/browser-report.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1;});
