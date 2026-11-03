import pytest

from scripts.qualify_prediction_training_tools import project_images


def test_each_project_keeps_its_own_pinned_image_with_shared_images_inspected_once():
    first, second = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    tasks = [
        {"repo": "one", "docker_image": first},
        {"repo": "two", "docker_image": second},
        {"repo": "three", "docker_image": first},
    ]
    observed = []

    def inspect(image):
        observed.append(image)
        return image + "\n"

    assert project_images(tasks, inspect) == {"one": first, "two": second, "three": first}
    assert observed == [first, second]


@pytest.mark.parametrize("image", ("mutable:latest", "sha256:short", ""))
def test_unpinned_original_images_fail_before_any_tool_execution(image):
    with pytest.raises(ValueError, match="immutable"):
        project_images([{"repo": "one", "docker_image": image}], lambda value: value)


def test_replaced_image_and_duplicate_project_fail_closed():
    tasks = [{"repo": "one", "docker_image": "sha256:" + "a" * 64}]
    with pytest.raises(ValueError, match="identity"):
        project_images(tasks, lambda _image: "sha256:" + "b" * 64)
    with pytest.raises(ValueError, match="distinct"):
        project_images(tasks * 2, lambda image: image)
