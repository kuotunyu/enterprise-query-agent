from pathlib import Path
import yaml

CATALOG = yaml.safe_load((Path(__file__).resolve().parents[2]/'catalog'/'metrics.yaml').read_text(encoding='utf-8'))
DEFINITIONS = {key:value['definition'] for key,value in CATALOG['metrics'].items()}
