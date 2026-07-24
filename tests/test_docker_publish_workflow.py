from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docker_publish_uses_the_versioned_shared_workflow() -> None:
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text()

    assert "workflow_run:" in workflow
    assert "- CI" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert (
        "github.event.workflow_run.head_branch == github.event.repository.default_branch"
        in workflow
    )
    assert (
        "adhatcher-org/shared-workflows/.github/workflows/"
        "docker-publish.yml@cc0291a44a46d85315af39d125bdc7f293b85b9b" in workflow
    )
    assert "package-name: bourbonbook" in workflow
    assert (
        "checkout-ref: ${{ github.event_name == 'workflow_run' && "
        "github.event.workflow_run.head_sha || github.sha }}" in workflow
    )
    assert "create-git-tag: true" in workflow
