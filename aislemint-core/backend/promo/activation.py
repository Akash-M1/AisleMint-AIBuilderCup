"""Atomically publish a reviewed model's asset pointers; historical blobs are retained."""
import hashlib,json
from datetime import datetime,timezone
from .pg import database,connection
from .storage import AssetPath,managed
from .jobs import training_lease
FILES=['model.joblib','evaluation.parquet','model_card.json']

def activate(source,kind='promotion'):
    source=managed(source)
    if not isinstance(source,AssetPath):raise ValueError('Activate a database model namespace, not a local file.')
    destination=AssetPath('artifacts/demand' if kind=='demand' else 'artifacts')
    card=json.loads((source/'model_card.json').read_text());old=json.loads((destination/'model_card.json').read_text())
    if card['model_id']==old['model_id']:return card['model_id']
    if card['panel_sha256']!=old['panel_sha256']:raise ValueError('Candidate belongs to a different dataset snapshot.')
    if hashlib.sha256((source/'model.joblib').read_bytes()).hexdigest()!=card['artifact_sha256']:raise ValueError('Candidate model checksum mismatch.')
    if kind=='demand':
        comparator=next(c['test']['wape'] for c in card['candidates'] if c['name']=='recent_mean')
        if card['test']['wape']>=comparator:raise ValueError('Candidate does not beat the recent-sales comparator.')
    else:
        if not card.get('recommendation_gate'):raise ValueError('Candidate failed the recommendation gate.')
        for fold in card['folds']:
            if fold['model']['wape']>=fold['incumbent_model']['wape'] or fold['calibration']['wape']>=fold['incumbent_calibration']['wape']:raise ValueError('Candidate must improve both calibration and held-out demand WAPE.')
    with training_lease:
        with connection() as con:
            conn=con.conn
            for name in FILES:
                current_row=conn.execute("SELECT * FROM assets WHERE key = %s",((destination/name).key,)).fetchone()
                new_row=conn.execute("SELECT * FROM assets WHERE key = %s",((source/name).key,)).fetchone()
                if not current_row or not new_row:raise ValueError('Model files are incomplete.')
                # Get column names
                desc=[d.name for d in conn.execute("SELECT * FROM assets LIMIT 0").description]
                current=dict(zip(desc,current_row));new=dict(zip(desc,new_row))
                # Backup current to versioned path
                backup_key=(destination/'versions'/old['model_id']/name).key
                conn.execute(
                    "INSERT INTO assets (key, kind, blob_id, sha256, size, compression, created) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (backup_key, current['kind'], current.get('blob_id'), current.get('sha256'), current.get('size'), current.get('compression'), current['created'])
                )
                # Replace current with new
                conn.execute(
                    "INSERT INTO assets (key, kind, blob_id, sha256, size, compression, created) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET kind=EXCLUDED.kind, blob_id=EXCLUDED.blob_id, "
                    "sha256=EXCLUDED.sha256, size=EXCLUDED.size, compression=EXCLUDED.compression, created=EXCLUDED.created",
                    ((destination/name).key, new['kind'], new.get('blob_id'), new.get('sha256'), new.get('size'), new.get('compression'), new['created'])
                )
            conn.execute(
                "INSERT INTO model_activations (kind, previous, model_id, created) VALUES (%s, %s, %s, %s)",
                (kind, old['model_id'], card['model_id'], datetime.now(timezone.utc))
            )
    return card['model_id']
