import tarfile
import typing

import container.registry


def iter_image_files(
    container_image_reference: str,
) -> typing.Iterable[typing.Tuple[typing.IO, str]]:
    with tarfile.open(
        mode='r|',
        fileobj=container.registry.retrieve_container_image(container_image_reference)
    ) as image_tarfile:
        for image_tar_info in image_tarfile:
            # we only care to scan files, obviously
            if not image_tar_info.isfile():
                continue
            if not image_tar_info.name.endswith('layer.tar'):
                continue # only layer files may contain relevant data
            with tarfile.open(
                mode='r|',
                fileobj=image_tarfile.extractfile(image_tar_info),
            ) as layer_tarfile:
                for layer_tar_info in layer_tarfile:
                    if not layer_tar_info.isfile():
                        continue
                    yield (
                        layer_tarfile.extractfile(layer_tar_info),
                        f'{image_tar_info.name}:{layer_tar_info.name}',
                    )
