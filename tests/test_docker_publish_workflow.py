from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_publish_uses_the_versioned_shared_workflow() -> None:
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text()

    assert "workflow_run:" in workflow
    assert "- CI" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == github.event.repository.default_branch" in workflow
    assert "adhatcher-org/shared-workflows/.github/workflows/docker-publish.yml@8189d4131e5b4b78d9b6f947e4a3bc8a28d4fdc8" in workflow
    assert "package-name: bourbonbook" in workflow
    assert "checkout-ref: ${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}" in workflow
    assert "create-git-tag: true" in workflow
