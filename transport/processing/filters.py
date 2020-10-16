import abc
import reutil


class FilterBase:
    @abc.abstractmethod
    def matches(self, component, container_image):
        pass


class MatchAllFilter(FilterBase):
    '''
    A filter matching everything. Use at the end of a processing.cfg
    '''
    def matches(self, component, container_image):
        return True


class ImageFilter(FilterBase):
    def __init__(
        self,
        include_image_refs=(),
        exclude_image_refs=(),
        include_image_names=(),
        exclude_image_names=(),
    ):
        self._image_ref_filter = reutil.re_filter(
            include_regexes=include_image_refs,
            exclude_regexes=exclude_image_refs,
            value_transformation=lambda image: image.access.imageReference,
        )
        self._image_name_filter = reutil.re_filter(
            include_regexes=include_image_names,
            exclude_regexes=exclude_image_names,
            value_transformation=lambda image: image.name,
        )

    def matches(self, component, container_image):
        return self._image_ref_filter(container_image) and \
            self._image_name_filter(container_image)


class ComponentFilter(FilterBase):
    def __init__(
        self,
        include_component_names=(),
        exclude_component_names=(),
    ):
        self._comp_name_filter = reutil.re_filter(
            include_regexes=include_component_names,
            exclude_regexes=exclude_component_names,
            value_transformation=lambda component: component.name,
        )

    def matches(self, component, container_image):
        return self._comp_name_filter(component)
