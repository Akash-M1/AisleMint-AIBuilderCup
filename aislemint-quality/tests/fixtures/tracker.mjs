import { DatabaseSync } from 'node:sqlite';
import { join } from 'node:path';
export const ids={paid:'AM-10000000-0000-4000-8000-000000000001',owner:'AM-10000000-0000-4000-8000-000000000002',sim:'AM-10000000-0000-4000-8000-000000000003',review:'AM-10000000-0000-4000-8000-000000000004',pending:'AM-10000000-0000-4000-8000-000000000005',mismatch:'AM-10000000-0000-4000-8000-000000000006'};
export function fixture(directory){
  const billingPath=join(directory,'billing.sqlite'), gatewayPath=join(directory,'gateway.sqlite');
  const b=new DatabaseSync(billingPath),g=new DatabaseSync(gatewayPath);
  b.exec(`CREATE TABLE orders(id TEXT PRIMARY KEY,plan_id,amount_paise,name,email,status,payment_id,created_at,paid_at,updated_at); CREATE TABLE events(payment_id,event,received_at,payload_hash);`);
  g.exec(`CREATE TABLE payment_sessions(id TEXT PRIMARY KEY,customer_id,attributed_observation_id,order_id,amount_paise,currency,status,verification_tag,completed_at);
    CREATE TABLE customers(id TEXT PRIMARY KEY,mobile_number,password_hash,otp_code);
    CREATE TABLE payment_observations(id TEXT PRIMARY KEY,source,bank_reference_number,observed_at,raw_payload);
    CREATE TABLE audit_logs(entity_id,action,created_at,details);
    CREATE TABLE webhook_deliveries(payment_id,event_type,response_status,attempt_count,is_successful,created_at,payload,response_body);
    CREATE TABLE payment_receiving_accounts(upi_id,payee_name,is_active,is_merchant_enabled);`);
  const now=new Date().toISOString();
  const data=[['paid','basic',1000,'Maya Kapoor','maya@example.test','SUCCESS'],['owner','premium',5000,'Arjun Foods','arjun@example.test','SUCCESS'],['sim','ultra',10000,'Test customer','test@example.test','SUCCESS'],['review','basic',1000,'=HYPERLINK("bad")','formula@example.test','SUCCESS'],['pending','premium',5000,'River Market','river@example.test','PENDING'],['mismatch','ultra',10000,'Deccan Retail','deccan@example.test','SUCCESS']];
  for(const [key,plan,amount,name,email,status] of data){
    const id=ids[key],payment='p-'+key,customer='c-'+key,observation='obs-'+key;
    b.prepare('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)').run(id,plan,amount,name,email,status,payment,now,status==='SUCCESS'?now:null,now);
    g.prepare('INSERT INTO customers VALUES(?,?,?,?)').run(customer,'919000000001','SECRET_PASSWORD_HASH','SECRET_OTP');
    g.prepare('INSERT INTO payment_sessions VALUES(?,?,?,?,?,?,?,?,?)').run(payment,customer,observation,id,amount,'INR',key==='pending'?'ACTIVE':key==='mismatch'?'FAILED':status,'Single Verification (Gmail)',now);
    g.prepare('INSERT INTO payment_observations VALUES(?,?,?,?,?)').run(observation,'GMAIL','123456789012',now,JSON.stringify({is_simulated:key==='sim',body:'SECRET_BANK_EMAIL'}));
    if(key==='owner'||key==='review')g.prepare('INSERT INTO audit_logs VALUES(?,?,?,?)').run(payment,'RECEIVER_CREDIT_RECONCILIATION_REQUIRED',now,'SECRET_AUDIT_DETAILS');
    if(key==='owner')g.prepare('INSERT INTO audit_logs VALUES(?,?,?,?)').run(payment,'OWNER_CONFIRMED_RECEIVER_CREDIT',now,'SECRET_AUDIT_DETAILS');
    g.prepare('INSERT INTO audit_logs VALUES(?,?,?,?)').run(payment,'PAYMENT_SLOT_ACTIVATED',now,'SECRET_AUDIT_DETAILS');
    g.prepare('INSERT INTO webhook_deliveries VALUES(?,?,?,?,?,?,?,?)').run(payment,status,200,1,1,now,'SECRET_WEBHOOK_PAYLOAD','SECRET_RESPONSE');
    b.prepare('INSERT INTO events VALUES(?,?,?,?)').run(payment,status,now,'SECRET_PAYLOAD_HASH');
  }
  g.prepare('INSERT INTO payment_receiving_accounts VALUES(?,?,?,?)').run('demo@bank','Preview merchant',1,1);
  b.exec(`CREATE TABLE fulfillments(order_id PRIMARY KEY,receipt_id,bank_reference,provision_status,
    receipt_sent_at,welcome_sent_at,receipt_attempts,provision_attempts,welcome_attempts,
    receipt_error,provision_error,welcome_error,result_json,created_at,updated_at,credential_cipher);`);
  b.prepare('INSERT INTO fulfillments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)').run(ids.paid,'AMR-PREVIEW','123456789012','READY',now,null,0,0,1,null,null,'EMAIL_DELIVERY_RETRY',
    JSON.stringify({starts_at:now,expires_at:new Date(Date.now()+365*86400000).toISOString(),temporary_password_expires_at:now}),now,now,'SECRET_ENCRYPTED_INVITATION');
  b.close();g.close();return{billingPath,gatewayPath};
}
export function mockFetch(){
  const state={admin:true,available:true};
  const token='a'.repeat(64);
  return {state,token,fetchImpl:async(url,options={})=>{
    if(!state.available)throw Error('Unavailable');
    if(url.endsWith('/auth/login')){const body=JSON.parse(options.body);return body.password==='preview-only-password'?new Response('{}',{headers:{'set-cookie':`hindsight_session=${token}; HttpOnly; Secure`}}):new Response('{}',{status:401});}
    if(url.endsWith('/auth/logout'))return new Response('{}');
    if(url.endsWith('/me'))return options.headers.Cookie===`hindsight_session=${token}`?Response.json({user:{name:'Demo administrator',email:'preview@example.test',admin:state.admin}}):new Response('{}',{status:401});
    if(url.endsWith('/server-health'))return Response.json({gmailHealthy:true,isWorking:true});
    if(url.endsWith('/whatsapp/status'))return Response.json({isConnected:true,qr:'SECRET_QR',token:'SECRET_TOKEN'});
    throw Error('Unexpected dependency URL');
  }};
}
