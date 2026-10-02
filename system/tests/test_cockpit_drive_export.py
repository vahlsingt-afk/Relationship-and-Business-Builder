import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "cockpit_drive_export", ROOT / "system/scripts/cockpit_drive_export.py")
drive_export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drive_export)


def test_export_updates_fixed_file_with_context_content(tmp_path):
    context = tmp_path / "context.json"
    payload = {"schema_version": "1.0", "projection_type": "generated_read_only"}
    context.write_text(json.dumps(payload), encoding="utf-8")
    request = Mock()
    request.execute.return_value = {"id": "fixed-drive-file", "size": "72"}
    files, service = Mock(), Mock()
    files.update.return_value = request
    service.files.return_value = files
    media_factory = Mock(return_value="MEDIA")
    result = drive_export.export_context(
        service=service, file_id="fixed-drive-file", context_path=context,
        media_factory=media_factory)
    media_factory.assert_called_once_with(str(context), mimetype="application/json", resumable=False)
    files.update.assert_called_once_with(
        fileId="fixed-drive-file", media_body="MEDIA", fields="id,modifiedTime,size")
    assert json.loads(context.read_text()) == payload
    assert result["id"] == "fixed-drive-file"
