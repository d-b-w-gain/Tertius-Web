from datetime import datetime, timezone
from uuid import uuid4

import pytest

from core.compile_messages import (
    CompileCommand,
    CompileResultPayload,
    CompileSourceFile,
    assert_message_size,
    compile_result_message_id,
    serialized_message_size,
)


def test_compile_command_serializes_source_bundle_and_request_id():
    command = CompileCommand(
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        requested_by=uuid4(),
        export_format="glb",
        created_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        files=[CompileSourceFile(filename="design.py", content="shape = 'queued'\n")],
        request_id="compile-request:test",
    )

    payload = command.model_dump_json()

    assert '"filename":"design.py"' in payload
    assert '"content":"shape = \'queued\'\\n"' in payload
    assert '"request_id":"compile-request:test"' in payload
    assert serialized_message_size(command) == len(payload.encode("utf-8"))


def test_compile_result_payload_serializes_artifact_metadata():
    result = CompileResultPayload(
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        export_format="stl",
        status="succeeded",
        artifact_content_base64="c29saWQ=",
        artifact_byte_size=5,
        artifact_content_type="model/stl",
        worker_started_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        worker_finished_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
    )

    payload = result.model_dump_json()

    assert '"status":"succeeded"' in payload
    assert '"artifact_byte_size":5' in payload
    assert '"artifact_content_base64":"c29saWQ="' in payload
    assert compile_result_message_id(result) == f"compile-result:{result.job_id}:succeeded"


def test_assert_message_size_rejects_oversized_payload():
    command = CompileCommand(
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        requested_by=uuid4(),
        export_format="stl",
        created_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        files=[CompileSourceFile(filename="design.py", content="x" * 100)],
        request_id="compile-request:test",
    )

    with pytest.raises(ValueError, match="request message"):
        assert_message_size(command, 20, "request")


def test_compile_command_round_trips_originating_llm_edit_job_id():
    llm_job_id = uuid4()
    command = CompileCommand(
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        requested_by=uuid4(),
        export_format="stl",
        created_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        files=[CompileSourceFile(filename="design.py", content="shape = 'queued'\n")],
        request_id="compile-request:test",
        originating_llm_edit_job_id=llm_job_id,
    )

    restored = CompileCommand.model_validate_json(command.model_dump_json())

    assert restored.originating_llm_edit_job_id == llm_job_id


def test_compile_command_originating_llm_edit_job_id_defaults_none_and_is_backward_compatible():
    payload = {
        "job_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "project_id": str(uuid4()),
        "requested_by": str(uuid4()),
        "export_format": "stl",
        "created_at": datetime(2026, 6, 14, tzinfo=timezone.utc).isoformat(),
        "files": [{"filename": "design.py", "content": "x = 1\n"}],
        "request_id": "compile-request:test",
    }

    command = CompileCommand.model_validate(payload)

    assert command.originating_llm_edit_job_id is None
