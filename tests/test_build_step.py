"""Tests for build artifact and verification step (Item 4.3)."""

import pytest
from enki.orch.build_step import (
    create_build_task,
    detect_build_needed,
    detect_mobile_project,
    format_build_section,
    BUILD_SECTION_TEMPLATE,
)


class TestCreateBuildTask:
    def test_basic_task(self):
        task = create_build_task(
            project="myapp",
            sprint_id="S1",
            build_commands=["npm run build"],
        )
        assert task["name"] == "Build & Verify"
        assert task["work_type"] == "build"
        assert task["agent"] == "devops"
        assert task["sprint_id"] == "S1"
        assert task["build_commands"] == ["npm run build"]
        assert task["dependencies"] == []
        assert task["expected_artifacts"] == []

    def test_with_dependencies(self):
        task = create_build_task(
            project="myapp",
            sprint_id="S1",
            build_commands=["cargo build"],
            depends_on=["T1", "T2"],
        )
        assert task["dependencies"] == ["T1", "T2"]

    def test_with_artifacts(self):
        task = create_build_task(
            project="myapp",
            sprint_id="S1",
            build_commands=["go build"],
            expected_artifacts=["bin/server", "bin/cli"],
        )
        assert task["expected_artifacts"] == ["bin/server", "bin/cli"]


class TestDetectBuildNeeded:
    def test_build_keyword(self):
        assert detect_build_needed("We need to build the Docker image") is True

    def test_webpack_keyword(self):
        assert detect_build_needed("Uses webpack for bundling") is True

    def test_cargo_keyword(self):
        assert detect_build_needed("Run cargo build to compile") is True

    def test_no_build(self):
        assert detect_build_needed("Simple Python script") is False

    def test_build_files_present(self):
        assert detect_build_needed("Some project", files=["Dockerfile"]) is True

    def test_makefile_present(self):
        assert detect_build_needed("Some project", files=["src/Makefile"]) is True

    def test_vite_config(self):
        assert detect_build_needed("Some project", files=["vite.config.ts"]) is True

    def test_no_build_files(self):
        assert detect_build_needed("Some project", files=["main.py", "utils.py"]) is False

    def test_gradle(self):
        assert detect_build_needed("Android project with gradle") is True


class TestDetectMobileProject:
    def test_android_keyword(self):
        assert detect_mobile_project("Build an Android app") is True

    def test_flutter_keyword(self):
        assert detect_mobile_project("Flutter mobile app") is True

    def test_react_native_keyword(self):
        assert detect_mobile_project("React Native cross-platform") is True

    def test_not_mobile(self):
        assert detect_mobile_project("Web dashboard in React") is False

    def test_mobile_tech_stack(self):
        assert detect_mobile_project(
            "Build app", tech_stack={"frameworks": ["flutter"]}
        ) is True

    def test_non_mobile_tech_stack(self):
        assert detect_mobile_project(
            "Build app", tech_stack={"frameworks": ["express"]}
        ) is False


class TestFormatBuildSection:
    def test_basic_format(self):
        result = format_build_section(
            build_commands=["npm install", "npm run build"],
            artifacts=["dist/"],
        )
        assert "npm install" in result
        assert "npm run build" in result
        assert "dist/" in result
        assert "Build & Verification" in result

    def test_no_artifacts(self):
        result = format_build_section(build_commands=["make"])
        assert "None specified" in result

    def test_with_verification(self):
        result = format_build_section(
            build_commands=["cargo build"],
            verification_steps=["Check binary exists", "Run smoke test"],
        )
        assert "Check binary exists" in result
        assert "Run smoke test" in result

    def test_empty_commands(self):
        result = format_build_section(build_commands=[])
        assert "TBD" in result
