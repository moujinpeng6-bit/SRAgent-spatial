"""Bounded archive/direct-asset preparation with explicit slice/bin grouping."""
import gzip
import hashlib
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from .biostudies import validate_prepared_files
from .hd_archive import spatial_bin_size
from .spatial import validate_visium, text_lines
from .asset_audit import decode_image
from .tenx_batch import checkpoint

MIB = 1024**2
ROLES = {
    'filtered_feature_bc_matrix.h5': 'h5', 'tissue_positions_list.csv': 'positions',
    'tissue_positions.csv': 'positions', 'tissue_positions.parquet': 'positions',
    'scalefactors_json.json': 'scalefactors', 'tissue_lowres_image.png': 'image',
    'barcodes.tsv.gz': 'barcodes', 'barcodes.tsv': 'barcodes',
    'features.tsv.gz': 'features', 'features.tsv': 'features',
    'matrix.mtx.gz': 'matrix', 'matrix.mtx': 'matrix',
    'cell_feature_matrix.h5': 'h5', 'cells.csv.gz': 'cells',
    'experiment.xenium': 'experiment', 'morphology_focus.ome.tif': 'image',
}
for _name,_role in list(ROLES.items()):
    if _name.endswith(('.h5','.csv','.json','.png','.parquet')):
        ROLES[_name+'.gz']=_role
HOSTS = {'ftp.ncbi.nlm.nih.gov', 'ftp.ebi.ac.uk', 'www.ebi.ac.uk', 'cf.10xgenomics.com'}

def checked_url(url):
    parts = urlsplit(url)
    if parts.scheme == 'ftp' and parts.hostname == 'ftp.ncbi.nlm.nih.gov':
        url = 'https://' + parts.netloc + parts.path
        parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc not in HOSTS or parts.username or parts.password
            or parts.fragment or any(x in {'..', '.'} for x in unquote(parts.path).split('/'))):
        raise ValueError('Only verified official source file URLs are accepted')
    return url

def role_and_group(name, accession=''):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe archive/file path')
    if '__MACOSX' in path.parts or path.name.startswith('._'):
        return None
    basename = path.name
    for suffix, role in sorted(ROLES.items(), key=lambda x: -len(x[0])):
        if basename != suffix and not basename.endswith('_' + suffix):
            continue
        # Full raw count folders must never be substituted for filtered counts.
        if any(part.startswith('raw_feature_bc_matrix') for part in path.parts):
            return None
        prefix = basename[:-len(suffix)].rstrip('_')
        if accession and prefix.startswith(accession + '_'):
            prefix = prefix[len(accession)+1:]
        elif prefix == accession:
            prefix = ''
        parent = path.parent
        if parent.name in {'spatial', 'filtered_feature_bc_matrix', 'cell_feature_matrix'}:
            parent = parent.parent
        group = str(parent) if str(parent) != '.' else ''
        if prefix:
            group = group + '/' + prefix if group else prefix
        return role, group or '.', suffix
    return None


class AssetPreparer:
    def __init__(self, out, roots=(), budget=128*MIB):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.roots = [Path(root).resolve() for root in roots if Path(root).is_dir()]
        self.budget, self.used = budget, 0
        self.index = None
        self.provenance = []

    def local(self, filename):
        if self.index is None:
            self.index = {}
            count = 0
            for root in self.roots:
                for path in root.rglob('*'):
                    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                        continue
                    count += 1
                    if count > 150000:
                        raise ValueError('Local inventory limit exceeded')
                    self.index.setdefault(path.name, set()).add(path.resolve())
        matches = self.index.get(filename, set())
        if len(matches) > 1:
            raise ValueError('Ambiguous local file: ' + filename)
        return next(iter(matches)) if matches else None

    def obtain(self, asset):
        url = checked_url(asset['url'])
        name = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
        if not name or name in {'.', '..'} or any(c in name for c in '/\\:'):
            raise ValueError('Unsafe source filename')
        # Generic names occur on many samples: only sample-prefixed published names
        # or exact archive names can be found by basename in unrelated directories.
        local = self.local(name) if (name.endswith(('.tar.gz', '.tgz', '.tar', '.zip')) or re.match(r'(GSM\d+|[A-Za-z0-9]+_.*)_', name)) else None
        if local:
            if asset.get('size') and local.stat().st_size != asset['size']:
                raise ValueError('Published file size differs from local candidate')
            self.provenance.append({'url': url, 'mode': 'exact_published_filename_local',
                'path': str(local), 'remote_byte_identity_verified': False})
            return local
        cache = self.out / 'downloads' / (hashlib.sha256(url.encode()).hexdigest()[:16] + '_' + name)
        metadata = cache.with_name(cache.name + '.source.json')
        if cache.exists() and metadata.exists():
            saved = json.loads(metadata.read_text(encoding='utf8'))
            if saved['url'] == url and saved['sha256'] == sha256(cache):
                self.provenance.append({**saved, 'mode': 'verified_download_cache'})
                return cache
        remaining = self.budget - self.used
        if remaining <= 0 or asset.get('size', 0) > remaining:
            raise OverflowError('Download budget insufficient for ' + name)
        cache.parent.mkdir(exist_ok=True)
        temporary = None
        try:
            with urlopen(Request(url, headers={'Accept-Encoding': 'identity'}), timeout=90) as response:
                checked_url(response.geturl())
                advertised = int(response.headers.get('Content-Length', '0'))
                if advertised > remaining:
                    raise OverflowError('Download exceeds remaining budget')
                with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    total = 0
                    while chunk := response.read(min(MIB, remaining - total + 1)):
                        self.used += len(chunk)
                        total += len(chunk)
                        if total > remaining:
                            raise OverflowError('Download budget exceeded')
                        stream.write(chunk)
                if not total or advertised and total != advertised:
                    raise OSError('Incomplete file transfer')
            temporary.replace(cache)
            saved = {'url': url, 'bytes': total, 'sha256': sha256(cache), 'mode': 'downloaded'}
            checkpoint(metadata, saved)
            self.provenance.append(saved)
            return cache
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def prepare(self, assets, accession, platform):
        groups, errors, unsupported = {}, [], []
        expanded = 0
        def save(name, source, size, evidence, archive_anchor=''):
            nonlocal expanded
            found = role_and_group(name, accession)
            if not found:
                return
            role, anchor, canonical = found
            if archive_anchor:
                anchor = archive_anchor + '/' + anchor
            if asset.get('pairing_key'):
                # Only an explicit source manifest may merge assets across packages.
                # Keep resolutions separate; duplicate roles remain an ambiguity.
                bins=re.findall(r'square_\d+um|\d{3}um',name)
                anchor=str(asset['pairing_key'])+('/'+bins[0] if bins else '')
            group = groups.setdefault(anchor, {})
            if role in group:
                group.setdefault('_duplicates', []).append(role)
                return
            if not 0 < size <= 256*MIB or expanded + size > 2*1024*MIB:
                group.setdefault('_limits', []).append(role)
                return
            expanded += size
            directory = self.out / 'groups' / hashlib.sha256(anchor.encode()).hexdigest()[:16]
            directory.mkdir(parents=True, exist_ok=True)
            dest = directory / canonical
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as staged:
                temporary = Path(staged.name)
                shutil.copyfileobj(source, staged, MIB)
            if temporary.stat().st_size != size:
                temporary.unlink()
                raise OSError('Archive member was truncated')
            temporary.replace(dest)
            compressed_hash=None
            if canonical.endswith(('.h5.gz','.png.gz','.parquet.gz')):
                compressed_hash=sha256(dest)
                unwrapped=dest.with_suffix('')
                written=0
                try:
                    with gzip.open(dest,'rb') as compressed, tempfile.NamedTemporaryFile(dir=directory,delete=False) as output:
                        staged=Path(output.name)
                        while chunk:=compressed.read(MIB):
                            written+=len(chunk)
                            if written>256*MIB or expanded+written>2*1024*MIB:
                                raise ValueError('Uncompressed asset size limit exceeded')
                            output.write(chunk)
                    staged.replace(unwrapped)
                except Exception:
                    if 'staged' in locals() and staged.exists(): staged.unlink()
                    raise
                expanded+=written
                dest=unwrapped
            group[role] = {'path': str(dest), 'sha256': sha256(dest), 'bytes': dest.stat().st_size,
                           'source_bytes':size,'compressed_source_sha256':compressed_hash, **evidence}

        for asset in assets[:100]:
            try:
                name = unquote(urlsplit(asset['url']).path.rsplit('/', 1)[-1])
                if not name.endswith(('.zip','.tar.gz','.tgz','.tar')) and not role_and_group(name,accession):
                    unsupported.append({'url':asset['url'],'reason':'not a recognized required spatial asset'})
                    continue
                path = self.obtain(asset)
                if name.endswith('.zip'):
                    with zipfile.ZipFile(path) as archive:
                        members = archive.infolist()
                        if len(members) > 100000:
                            raise ValueError('ZIP member limit exceeded')
                        for member in members:
                            if member.is_dir():
                                continue
                            if role_and_group(member.filename, accession):
                                with archive.open(member) as stream:
                                    save(member.filename, stream, member.file_size,
                                         {'source_url': asset['url'], 'member': member.filename}, archive_anchor=name)
                elif name.endswith(('.tar.gz', '.tgz', '.tar')):
                    with tarfile.open(path, 'r|*') as archive:
                        for index, member in enumerate(archive):
                            if index > 100000:
                                raise ValueError('TAR member limit exceeded')
                            # Never extract links, devices, absolute paths or traversal.
                            if not member.isfile():
                                if member.issym() or member.islnk():
                                    raise ValueError('Archive links are not accepted')
                                continue
                            found = role_and_group(member.name, accession)
                            if found:
                                with archive.extractfile(member) as stream:
                                    save(member.name, stream, member.size,
                                         {'source_url': asset['url'], 'member': member.name}, archive_anchor=name)
                elif role_and_group(name, accession):
                    with path.open('rb') as stream:
                        save(name, stream, path.stat().st_size, {'source_url': asset['url']})
            except (OSError, ValueError, KeyError, OverflowError, zipfile.BadZipFile, tarfile.TarError) as exc:
                errors.append({'url': asset['url'], 'error_type': type(exc).__name__, 'reason': str(exc)})
        prepared = []
        for anchor, roles in groups.items():
            required = ({'h5', 'cells', 'experiment', 'image'} if platform == 'Xenium' else
                        ({'h5'} if 'h5' in roles else {'barcodes', 'features', 'matrix'}) | {'positions', 'scalefactors', 'image'})
            missing = sorted(required - roles.keys())
            status = ('ambiguous_bundle' if set(roles.get('_duplicates',[])) & required else 'file_limit_exceeded' if set(roles.get('_limits',[])) & required else
                      'missing_files' if missing else 'files_prepared')
            prepared.append({'anchor': anchor, 'status': status, 'missing_roles': missing,
                'files': {k:v for k,v in roles.items() if not k.startswith('_')}, 'duplicates': roles.get('_duplicates', [])})
        return {'groups': prepared, 'errors': errors, 'unsupported_assets':unsupported,'network_bytes': self.used, 'provenance': self.provenance,
                'unread_assets_due_to_limit': max(0, len(assets)-100)}

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(MIB):
            digest.update(chunk)
    return digest.hexdigest()

def validate_group(row, platform, out):
    paths = {role: Path(asset['path']) for role, asset in row['files'].items()}
    if any(not path.is_file() or sha256(path) != row['files'][role]['sha256'] for role, path in paths.items()):
        return {'status': 'missing_or_changed_files', 'files_ready': False}
    if platform == 'Xenium':
        from .xenium_validation import validate_xenium
        report = validate_xenium({'sample_id': 'sample', 'platform': 'Xenium', 'coordinate_unit': 'um',
            'data_dir': str(paths['h5'].parent), 'image': str(paths['image'])}, out)
        return {'status': report['status'], 'files_ready': True, 'checks': report['checks'],
                'cells': report['cells'], 'coordinate_unit': 'um', 'pixel_size_um': report['pixel_size_um']}
    if platform=='Visium HD' and row.get('matrix_scope')=='header_only':
        from .hd_archive import validate_hd_bin
        result=validate_hd_bin({role:{'path':path,'member':row['files'][role].get('member',path.name)} for role,path in paths.items()},out,row['bin_um'])
        return {**result,'files_ready':result['status']!='missing_files','matrix_scope':'header_only',
                'not_verified':['full count entries and feature identities']}
    if 'h5' in paths:
        if platform == 'Visium HD' and paths['positions'].suffix != '.parquet':
            raise ValueError('HD requires declared bin-resolution positions')
        return validate_prepared_files(paths)
    size = spatial_bin_size(paths['scalefactors']) if platform == 'Visium HD' else None
    basic = validate_visium(paths['barcodes'], paths['positions'], paths['scalefactors'], paths['image'],
                            paths['matrix'], bin_um=size, full_matrix=True)
    feature_count = sum(1 for _ in text_lines(paths['features']))
    opener = gzip.open if str(paths['matrix']).endswith('.gz') else open
    with opener(paths['matrix'], 'rt') as stream:
        dimensions = next(line for line in stream if line.strip() and not line.startswith('%')).split()
    if feature_count != int(dimensions[0]):
        basic['issues'].append('feature_rows_do_not_match_matrix')
    return {'status': 'mismatch' if basic['issues'] else 'correspondence_passed', 'files_ready': True,
            'checks': basic, 'image_decode': decode_image(paths['image'])}
