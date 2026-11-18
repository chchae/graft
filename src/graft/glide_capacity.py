"""Read-only SLM capacity and conservative native Glide CPU reservations."""
import re
import subprocess
from pathlib import Path

from graft.config import SCHRODINGER_LICENSE_SERVERS, schrodinger_environment


def parse_license_status(text):
    features = {}
    for feature in ('GLIDE_MAIN', 'GLIDE_SP_DOCKING'):
        pattern = (r'^Users of '+feature+r': \(Total of (\d+) licenses issued; '
                   r'Total of (\d+) licenses in use; Total of (\d+) licenses available\)\s*$')
        matches = re.findall(pattern, text, re.M)
        if len(matches) != 1:
            raise ValueError(f'Cannot verify SLM feature {feature}')
        total, used, available = map(int, matches[0])
        if total != used+available:
            raise ValueError('Inconsistent SLM feature counts')
        features[feature] = dict(total=total, available=available)
    return dict(total=min(features['GLIDE_MAIN']['total'], features['GLIDE_SP_DOCKING']['total']//4),
                available=min(features['GLIDE_MAIN']['available'], features['GLIDE_SP_DOCKING']['available']//4))


def license_capacity(suite):
    """Floor per server: two servers with two tokens each cannot fund one job."""
    values = []
    for server in dict.fromkeys(SCHRODINGER_LICENSE_SERVERS.split(';')):
        result = subprocess.run([str(Path(suite)/'internal/bin/lictool'), 'status', '--licsrv-hostport', server],
                                check=True, capture_output=True, text=True, timeout=30,
                                env=schrodinger_environment())
        values.append(parse_license_status(result.stdout))
    return {key: sum(v[key] for v in values) for key in ('total', 'available')}
