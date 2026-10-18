"""Renewable PostgreSQL training lease shared by all API and worker processes."""
from datetime import datetime,timedelta,timezone
import secrets,threading,time
from .pg import operation_connection,retry_transaction

class TrainingLease:
    def __init__(self,name='model-training'):
        self.name=name;self.owner=None;self.stop=None;self.guard=threading.Lock();self.lost=False
    @retry_transaction
    def _claim(self,owner):
        with operation_connection() as conn:
            return conn.execute(
                'INSERT INTO leases (_id,owner,expires_at) VALUES (%s,%s,now()+interval \'120 seconds\') '
                'ON CONFLICT (_id) DO UPDATE SET owner=EXCLUDED.owner,expires_at=EXCLUDED.expires_at '
                'WHERE leases.expires_at <= now() RETURNING owner',(self.name,owner)).fetchone()
    def acquire(self,blocking=True):
        while True:
            with self.guard:
                if self.owner is None:
                    owner=secrets.token_hex(16)
                    if self._claim(owner):
                        self.owner=owner;self.lost=False;self.stop=threading.Event()
                        threading.Thread(target=self._renew,args=(owner,self.stop),daemon=True).start();return True
            if not blocking:return False
            time.sleep(.25)
    def _renew(self,owner,stop):
        while not stop.wait(20):
            try:
                with operation_connection() as conn:
                    cur=conn.execute("UPDATE leases SET expires_at=now()+interval '120 seconds' WHERE _id=%s AND owner=%s AND expires_at>now()",(self.name,owner))
                    if cur.rowcount==0:self.lost=True;return
            except Exception:self.lost=True;return
    def assert_owned(self):
        if self.owner:
            with operation_connection() as conn:
                owned=conn.execute('SELECT 1 FROM leases WHERE _id=%s AND owner=%s AND expires_at>now()', (self.name,self.owner)).fetchone()
            if self.lost or not owned:raise RuntimeError('Training worker lease expired; candidate publishing was stopped. Retry training from a fresh snapshot.')
    def release(self):
        with self.guard:
            if self.stop:self.stop.set()
            try:
                if self.owner:
                    with operation_connection() as conn:conn.execute('DELETE FROM leases WHERE _id=%s AND owner=%s',(self.name,self.owner))
            finally:self.owner=None;self.stop=None
    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()

training_lease=TrainingLease()
