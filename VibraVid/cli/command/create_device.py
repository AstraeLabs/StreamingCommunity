# 29.07.25

import sys
from pathlib import Path
from zlib import crc32

from rich.console import Console
from pyplayready.device import Device as PR_Device
from pyplayready.cdm import Cdm as PR_Cdm
from pyplayready.system.pssh import PSSH as PR_PSSH
from pyplayready.crypto.ecc_key import ECCKey as PR_ECCKey
from pyplayready.system.bcert import CertificateChain as PR_CertificateChain, Certificate as PR_Certificate, BCertCertType as PR_BCertCertType
from Crypto.Random import get_random_bytes

from pywidevine.device import Device as WV_Device, DeviceTypes as WV_DeviceTypes
from pywidevine.cdm import Cdm as WV_Cdm
from pywidevine.pssh import PSSH as WV_PSSH
from unidecode import unidecode

from VibraVid.utils.http_client import create_client


console = Console()


def _ensure_file(path: str) -> Path:
    p = Path(path)
    if not p.exists():
        console.print(f"[red]File not found: {path}")
        sys.exit(2)
    return p


def create_playready_device(args):
    """Create a PlayReady .prd device from bgroupcert and zgpriv (provisioning)."""
    cert_p = _ensure_file(args.cert)
    key_p = _ensure_file(args.key)
    out_p = Path(args.output) if args.output else None

    try:
        group_key = PR_ECCKey.load(str(key_p))
        certificate_chain = PR_CertificateChain.load(str(cert_p))

        # Check if already provisioned
        if certificate_chain.get(0).get_type() == PR_BCertCertType.DEVICE:
            console.print("[yellow]Warning: This certificate chain already contains a DEVICE certificate.")

        # Provision new keys (like CLI does)
        encryption_key = PR_ECCKey.generate()
        signing_key = PR_ECCKey.generate()

        new_certificate = PR_Certificate.new_leaf_cert(
            cert_id=get_random_bytes(16),
            security_level=certificate_chain.get_security_level(),
            client_id=get_random_bytes(16),
            signing_key=signing_key,
            encryption_key=encryption_key,
            group_key=group_key,
            parent=certificate_chain
        )
        certificate_chain.prepend(new_certificate)

        device = PR_Device(
            group_key=group_key.dumps(),
            encryption_key=encryption_key.dumps(),
            signing_key=signing_key.dumps(),
            group_certificate=certificate_chain.dumps(),
        )

        # Force version 3 for dump to include group key (v2 would omit it)
        # This matches pyplayready 0.8.x defaults
        wvd_bin = device.dumps(version=3)

        if out_p and out_p.suffix:
            final_out = out_p
        else:
            out_dir = out_p or Path.cwd()
            final_out = out_dir / f"{device.get_name()}.prd"

        final_out.write_bytes(wvd_bin)

        console.print(f"[green]\nCreated Playready Device (.prd) file, {final_out.name}")
        console.print(f" + Security Level: {device.security_level}")
        console.print(f" + Group Key: {len(device.group_key.dumps())} bytes")
        console.print(f" + Encryption Key: {len(device.encryption_key.dumps())} bytes")
        console.print(f" + Signing Key: {len(device.signing_key.dumps())} bytes")
        console.print(f" + Group Certificate: {len(device.group_certificate.dumps())} bytes")
        console.print(f" + Saved to: {final_out.absolute()}")
        return 0
    
    except Exception as e:
        console.print(f"[red]Error creating PlayReady device: {e}")
        return 0
    
    except Exception as e:
        console.print(f"[red]Error creating PlayReady device: {e}")
        sys.exit(1)


def export_prd_device(prd_path: str, output_dir: str = "."):
    """Export a .prd device into original bgroupcert and zgpriv files."""
    prd_file = Path(prd_path)
    try:
        device = PR_Device.load(str(prd_file))

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        bgroupcert_out = out / "bgroupcert_exported.dat"
        zgpriv_out = out / "zgpriv_exported.dat"

        with open(bgroupcert_out, "wb") as f:
            f.write(getattr(device, 'bgroupcert', b""))

        with open(zgpriv_out, "wb") as f:
            f.write(getattr(device, 'zgpriv', b""))

        console.print(f"[green]Exported files to: {out}")
        return 0
    
    except Exception as e:
        console.print(f"[red]Export failed: {e}")
        sys.exit(1)


def create_widevine_device(args):
    """Create a Widevine .wvd device from private key and client id (matching pywidevine CLI)"""
    key_p = _ensure_file(args.private_key)
    client_p = _ensure_file(args.client_id)
    out_p = Path(args.output) if args.output else None
    
    # Map L1/L3 to 1/3
    sec = int(args.security_level.replace('L', '')) if hasattr(args, 'security_level') else 3

    try:
        key_bytes = key_p.read_bytes()
        client_bytes = client_p.read_bytes()

        device = WV_Device(
            type_=WV_DeviceTypes.ANDROID,
            security_level=sec,
            flags=None,
            private_key=key_bytes,
            client_id=client_bytes
        )
        
        client_info = {}
        for entry in device.client_id.client_info:
            client_info[entry.name] = entry.value

        wvd_bin = device.dumps()

        name = f"{client_info['company_name']} {client_info['model_name']}"
        if client_info.get("widevine_cdm_version"):
            name += f" {client_info['widevine_cdm_version']}"
        name += f" {crc32(wvd_bin).to_bytes(4, 'big').hex()}"

        try:
            name = unidecode(name.strip().lower().replace(" ", "_"))
        except Exception:
            name = name.strip().lower().replace(" ", "_")

        if out_p and out_p.suffix:
            final_out = out_p
        else:
            out_dir = out_p or Path.cwd()
            final_out = out_dir / f"{name}_{device.system_id}_l{device.security_level}.wvd"

        final_out.parent.mkdir(parents=True, exist_ok=True)
        final_out.write_bytes(wvd_bin)

        console.print(f"[green]\nCreated Widevine Device (.wvd) file, {final_out.name}")
        console.print(f" + Type: {device.type.name}")
        console.print(f" + System ID: {device.system_id}")
        console.print(f" + Security Level: {device.security_level}")
        console.print(f" + Flags: {device.flags}")
        console.print(f" + Private Key: {bool(device.private_key)} ({device.private_key.size_in_bits()} bit)")
        console.print(f" + Client ID: {bool(device.client_id)} ({len(device.client_id.SerializeToString())} bytes)")
        console.print(f" + VMP: {bool(device.client_id.vmp_data)}")
        console.print(f" + Saved to: {final_out.absolute()}")
        return 0
    
    except Exception as e:
        console.print(f"[red]Error creating Widevine device: {e}")
        sys.exit(1)


def export_wvd_device(wvd_path: str, output_dir: str = "."):
    """Export a .wvd device into private key and client id files."""
    wvd_file = Path(wvd_path)
    try:
        device = WV_Device.load(str(wvd_file))

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        private_key_out = out / "private_key_exported.pem"
        client_id_out = out / "client_id_exported.bin"

        if hasattr(device.private_key, 'export_key'):
            private_key_bytes = device.private_key.export_key(format='PEM')
        else:
            private_key_bytes = device.private_key if isinstance(device.private_key, bytes) else b""

        with open(private_key_out, "wb") as f:
            f.write(private_key_bytes)

        if hasattr(device.client_id, 'SerializeToString'):
            client_id_bytes = device.client_id.SerializeToString()
        else:
            client_id_bytes = device.client_id if isinstance(device.client_id, bytes) else b""

        with open(client_id_out, "wb") as f:
            f.write(client_id_bytes)

        console.print(f"[green]Exported files to: {out}")
        return 0
    
    except Exception as e:
        console.print(f"[red]Export failed: {e}")
        sys.exit(1)


def migrate_device(input_path: str, output_path: str = None):
    """Migrate a .wvd device to the latest format."""
    try:
        if not output_path:
            output_path = input_path.replace(".wvd", ".v2.wvd")
        
        console.print(f"[cyan]Migrating Widevine device: {input_path}")
        device = WV_Device.load(input_path)
        device.dump(output_path)
        console.print(f"[green]Device migrated to: {output_path}")
        return 0
    except Exception as e:
        console.print(f"[red]Migration failed: {e}")
        sys.exit(1)


def test_device(wvd_path: str, privacy_mode: bool = False):
    """Test a WVD device by requesting a license from a demo server."""
    device = WV_Device.load(str(wvd_path))
    cdm = WV_Cdm.from_device(device)
    session_id = cdm.open()

    pssh_b64 = "...."
    license_url = "...."
    
    pssh = WV_PSSH(pssh_b64)
    challenge = cdm.get_license_challenge(session_id, pssh, privacy_mode=privacy_mode)

    response = create_client().post(license_url, data=challenge)
    response.raise_for_status()

    cdm.parse_license(session_id, response.content)

    console.print("[green]Widevine test completed successfully — keys:")
    for key in cdm.get_keys(session_id):
        if key.type != 'CONTENT':
            continue
        
        kid = key.kid.hex.lower().strip()
        key_val = key.key.hex().lower().strip()
        console.print(f"  - [red]{kid}[white]:[green]{key_val}")

    cdm.close(session_id)
    return 0


def test_playready_device(args):
    """Test a PlayReady .prd device using provided PSSH and license URL."""
    device_p = _ensure_file(args.device)
    pssh_b64 = "...."
    license_url = "...."

    device = PR_Device.load(str(device_p))
    cdm = PR_Cdm.from_device(device)
    session_id = cdm.open()

    pssh = PR_PSSH(pssh_b64)
    if not pssh.wrm_headers:
        console.print("[red]No WRM headers found in PSSH")
        sys.exit(3)

    challenge = cdm.get_license_challenge(session_id, pssh.wrm_headers[0])

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    resp = create_client(headers=headers).post(license_url, data=challenge)
    resp.raise_for_status()

    cdm.parse_license(session_id, resp.text)

    console.print("[green]PlayReady test completed — keys:")
    for key_obj in cdm.get_keys(session_id):
        kid = key_obj.key_id.hex.replace('-', '').lower().strip()
        key_val = key_obj.key.hex().replace('-', '').strip()
        console.print(f"    - [red]{kid}[white]:[green]{key_val}")

    cdm.close(session_id)
    return 0