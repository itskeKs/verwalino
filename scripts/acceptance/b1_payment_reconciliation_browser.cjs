const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {randomUUID}=require('node:crypto');
const {chromium}=require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base='http://127.0.0.1:13000', output='/tmp/hv-b1-reconciliation-acceptance';
(async()=>{
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.HV_CHROMIUM_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000);
 const report={checks:[],pageErrors:[],responses:[]};
 page.on('pageerror',e=>report.pageErrors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)report.responses.push({status:r.status(),path:new URL(r.url()).pathname});});
 try{
  await page.goto(base+'/login');await page.getByLabel('E-Mail').fill('admin@alpha.de');await page.getByLabel('Passwort').fill('testpass1234');await page.getByRole('button',{name:'Anmelden',exact:true}).click();await page.waitForURL(u=>!u.pathname.includes('login'));
  const post=async(path,data,keyed=false,status=201)=>{const r=await page.request.post(base+path,{data,headers:keyed?{'Idempotency-Key':randomUUID()}: {}});assert.equal(r.status(),status,await r.text());return status===204?null:r.json();};
  const admin=(await(await page.request.get(base+'/api/users/page?eligible_for_scope=true&q=admin%40alpha.de')).json()).items[0];
  const property=await post('/api/properties',{name:'Legacy payment QA '+Date.now(),street:'Test 1',zip_code:'12345',city:'Test',management_type:'MIET'});
  const bank=await post('/api/bank-accounts',{property_id:property.id,account_type:'LAUFEND',label:'Legacy QA bank'});
  const person=await post('/api/persons',{last_name:'Legacy payment QA'});
  const tenant=await post('/api/tenant-profiles',{person_id:person.id});
  const originals=[];
  for(const [value_date,amount] of [['2025-06-01','100.00'],['2024-06-01','200.00']]){
   const payment=await post('/api/payments',{bank_account_id:bank.id,tenant_profile_id:tenant.id,direction:'IN',amount,value_date,payment_type:'KALTMIETE',period_year:2025,period_month:6});
   originals.push(await post(`/api/payments/${payment.id}/book`,{},false,200));
  }
  const scope=await post('/api/accounting-scopes',{property_id:property.id,scope_type:'MIET',label:'Legacy payment review',valid_from:'2025-01-01',valid_to:'2025-12-31',whole_property:true,units:[]},true);
  const grant=await post(`/api/accounting-scopes/${scope.id}/grants`,{user_id:admin.id,capabilities:['read','prepare'],reason:'Synthetic review grant'},true);
  await post(`/api/accounting-scopes/${scope.id}/bindings`,{resource_type:'bank_account',resource_id:bank.id,reason:'Synthetic reviewed original bank'});
  const endpoint=`/api/accounting-scopes/${scope.id}/legacy-payments`;
  await page.goto(base+'/berechtigungen/'+scope.id);await page.getByRole('button',{name:'Altzahlungen prüfen',exact:true}).click();
  const selected=page.getByRole('checkbox',{name:'Zahlung Legacy QA bank 2025-06-01 auswählen',exact:true});await selected.waitFor();
  assert.equal(await page.getByRole('checkbox',{name:'Zahlung Legacy QA bank 2024-06-01 auswählen',exact:true}).isDisabled(),true);
  await page.getByText('Zeitraum, Mandat oder Zahlungsart passen nicht zum Kreis',{exact:true}).waitFor();await selected.check();await page.getByLabel('Begründung der Übernahme',{exact:true}).fill('Originalkonto und Buchungen geprüft');
  report.checks.push('explicit preview separates the proven bank descendant from a blocked historical period; only proven rows can be selected');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('aside').getBoundingClientRect().right<=1);await selected.scrollIntoViewIfNeeded();report.mobile=await page.locator('main').evaluate(n=>({visible:n.clientWidth,content:n.scrollWidth}));assert.ok(report.mobile.content<=report.mobile.visible+1);await page.screenshot({path:output+'/reconciliation-mobile.png'});
  const posts=[];let committed;
  await page.route('**'+endpoint,async route=>{
   if(route.request().method()!=='POST'){await route.continue();return;}
   posts.push({key:route.request().headers()['idempotency-key'],body:route.request().postData()});
   if(posts.length===1){const response=await route.fetch();assert.equal(response.status(),201,await response.text());committed=await response.json();await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Antwort nach erfolgreicher Übernahme unterbrochen'})});}
   else await route.continue();
  });
  const apply=page.getByRole('button',{name:'Auswahl verbindlich zuordnen',exact:true});await apply.click();await page.getByRole('alert').filter({hasText:'Antwort nach erfolgreicher Übernahme unterbrochen'}).waitFor();assert.equal(await selected.isChecked(),true);await apply.click();await page.getByText('1 Zahlungen wurden zugeordnet. Die ursprünglichen Buchungen bleiben erhalten.',{exact:true}).waitFor();assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
  const replay=await page.request.post(base+endpoint,{data:JSON.parse(posts[0].body),headers:{'Idempotency-Key':posts[0].key}});assert.equal(replay.status(),201);assert.deepEqual(await replay.json(),committed);
  const after=await(await page.request.get(base+'/api/payments/'+originals[0].id)).json();assert.deepEqual(after,originals[0]);
  const remaining=(await(await page.request.get(base+endpoint)).json()).items;assert.equal(remaining.length,1);assert.equal(remaining[0].id,originals[1].id);assert.equal(remaining[0].proof_status,'review_required');report.checks.push('lost committed response retries with the same command and returns the identical receipt; original payment is unchanged and the unresolved row remains');
  await post(`/api/accounting-scopes/${scope.id}/grants/${grant.id}/revoke`,{},false,204);await page.getByRole('button',{name:'Vorschau neu laden',exact:true}).click();await page.getByRole('alert').filter({hasText:'Vorschau nicht verfügbar'}).waitFor();assert.equal(await page.getByText(/Keine weiteren Altzahlungen/).count(),0);assert.equal((await page.request.post(base+endpoint,{data:JSON.parse(posts[0].body),headers:{'Idempotency-Key':posts[0].key}})).status(),404);report.checks.push('revocation blocks preview and receipt replay without an empty-success message');
  assert.deepEqual(report.pageErrors,[]);assert.ok(report.responses.every(r=>(r.status===401&&r.path==='/api/auth/session')||(r.status===409&&r.path==='/api/fristen')||([503,404].includes(r.status)&&r.path===endpoint)),JSON.stringify(report.responses));report.status='passed';
 }finally{if(report.status!=='passed')await page.screenshot({path:output+'/failed-state.png',fullPage:true});await fs.writeFile(output+'/browser-report.json',JSON.stringify(report,null,2));await browser.close();}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exitCode=1;});
