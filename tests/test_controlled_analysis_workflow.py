from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "controlled-analysis.yml"


def _step(workflow: str, start: str, end: str) -> str:
    return workflow.split(f"- name: {start}", 1)[1].split(f"- name: {end}", 1)[0]


def test_controlled_workflow_binds_the_complete_fear_greed_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "run-name: Controlled Fear & Greed · ${{ inputs.control_run_id }}" in workflow
    assert "analysis_inputs_json:" in workflow
    assert "all 17 Fear & Greed control fields" in workflow
    assert "control_inputs_json:" not in workflow
    assert "--analysis-inputs-json" in workflow
    assert "--allow-fallback" in workflow
    assert '[[ "${ALLOW_FALLBACK}" != "false" ]]' in workflow
    assert "fear-greed/control-inputs-v1" in workflow
    assert "70df5e68d4ecae4ad93fa410ccd74f2a12ee3d2ca0bfcba2ae2074de284c2e61" in workflow
    assert "fear-greed-json-sort-keys-sha256-v1" in workflow
    assert '--code-version "github:SonChangGi/fearNgreed@${ANALYSIS_SHA}"' in workflow
    assert "python -m fearngreed.control_run" in workflow

    input_names = (
        "analysis_inputs_json",
        "allow_fallback",
        "control_run_id",
        "control_input_schema_version",
        "control_input_schema_hash",
        "control_config_hash_algorithm",
        "control_config_hash",
    )
    dispatch_inputs = workflow.split("concurrency:", 1)[0]
    for input_name in input_names:
        assert dispatch_inputs.count(f"      {input_name}:") == 1


def test_controlled_workflow_commits_only_an_immutable_result_path() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    commit = _step(
        workflow,
        "Commit only the immutable controlled result",
        "Build the exact public site",
    )

    assert 'git add -- "${ARTIFACT_PATH}"' in commit
    assert "\n          git add data\n" not in commit
    assert "\n          git add .\n" not in commit
    assert "data/control-runs/v1/" in commit
    assert "[0-9a-f]{{64}}" in commit
    assert 'git ls-files --error-unmatch -- "${ARTIFACT_PATH}"' in commit
    assert '[[ "${staged_paths}" != "${ARTIFACT_PATH}" ]]' in commit
    assert commit.index("git diff --cached --quiet") < commit.index(
        'staged_paths="$(git diff --cached --name-only)"'
    )
    assert "git push origin HEAD:main" in commit
    assert "git push --force" not in commit
    assert "git reset" not in commit
    assert workflow.index("Record the analysis code revision") < workflow.index(
        "Validate the full request and run the existing Python engine"
    )
    assert workflow.index("Validate the full request and run the existing Python engine") < (
        workflow.index("Commit only the immutable controlled result")
    )


def test_controlled_workflow_serializes_deploy_and_verifies_exact_public_bytes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "group: fearngreed-pages" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert '[[ "${GITHUB_REF_NAME}" != "main" ]]' in workflow
    assert "actions/configure-pages@v5" in workflow
    assert "actions/upload-pages-artifact@v3" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert workflow.index("Commit only the immutable controlled result") < workflow.index(
        "Build the exact public site"
    )
    assert workflow.index("Build the exact public site") < workflow.index(
        "Deploy the exact public site"
    )
    assert workflow.index("Deploy the exact public site") < workflow.index(
        "Verify the exact public artifact bytes"
    )
    assert workflow.index("Verify the exact public artifact bytes") < workflow.index(
        "Publish the verified result manifest to the control API"
    )

    verification = _step(
        workflow,
        "Verify the exact public artifact bytes",
        "Publish the verified result manifest to the control API",
    )
    assert "artifact_sha256" in verification
    assert "artifact_byte_size" in verification
    assert "sha256sum" in verification
    assert "wc -c" in verification
    assert "--proto '=https'" in verification
    assert "Cache-Control: no-cache" in verification


def test_controlled_workflow_scopes_callback_secrets_and_reports_failure() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    before_success_callback = workflow.split(
        "- name: Publish the verified result manifest to the control API",
        1,
    )[0]
    success_callback = _step(
        workflow,
        "Publish the verified result manifest to the control API",
        "Report a controlled-run failure",
    )
    failure_callback = workflow.split("- name: Report a controlled-run failure", 1)[1]

    for secret in (
        "QUANT_CONTROL_API_BASE_URL",
        "QUANT_CONTROL_WORKER_CALLBACK_TOKEN",
    ):
        assert secret not in before_success_callback
        assert secret in success_callback
        assert secret in failure_callback
    assert "/v1/internal/runs/${CONTROL_RUN_ID}/result-manifest" in success_callback
    assert '--data-binary "@${MANIFEST_PATH}"' in success_callback
    assert 'parsed.scheme != "https"' in success_callback
    assert "parsed.username" in success_callback
    assert "parsed.password" in success_callback
    assert "parsed.query" in success_callback
    assert "parsed.fragment" in success_callback

    assert "if: ${{ always() && failure() }}" in failure_callback
    assert "continue-on-error: true" in failure_callback
    assert '"projectId": "fear-greed"' in failure_callback
    assert '"errorCode": "worker_workflow_failed"' in failure_callback
    assert '"providerRunId": f"github-actions:{run_id}"' in failure_callback
    assert "/v1/internal/runs/${CONTROL_RUN_ID}/failure" in failure_callback
    assert "--proto '=https'" in failure_callback


def test_controlled_workflow_does_not_replace_periodic_or_public_workflows() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "\n  schedule:" not in workflow
    assert "KRX_API_KEY" not in workflow
    assert "KRX_ID" not in workflow
    assert "KRX_PW" not in workflow

    for filename in ("refresh.yml", "live-signal.yml", "pages.yml"):
        existing = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert "fearngreed.control_run" not in existing
        assert "analysis_inputs_json" not in existing
