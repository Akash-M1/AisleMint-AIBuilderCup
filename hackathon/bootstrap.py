from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
subprocess.run(['npm','ci'],cwd=root/'aislemint-platform/frontend',check=True)
for name in ('core','admin','promotions','demand','sales','data','connect'):
 link=root/f'aislemint-{name}/frontend/node_modules'
 if not link.exists():link.symlink_to('../../aislemint-platform/frontend/node_modules',target_is_directory=True)
print('Frontend dependencies installed. Source is entirely inside this repository.')
