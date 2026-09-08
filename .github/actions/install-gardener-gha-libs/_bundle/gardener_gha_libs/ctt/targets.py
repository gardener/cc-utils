import abc


class TargetBase:
    @abc.abstractmethod
    def filter(
        self,
        tgt_oci_registry: str,
    ) -> bool:
        '''
        Returns `True` in case the specified `tgt_oci_registry` is configured by the instance of
        `Target`. This filter is used to process (i.e. replicate) only those resources which are
        configured for the currently processed `tgt_oci_registry`.
        '''
        raise NotImplementedError('must be implemented by its subclasses')


class RegistriesTarget(TargetBase):
    def __init__(
        self,
        registries: list[str],
        ocm_repository: str | None=None,
    ):
        self._registries = registries
        self._ocm_repository = ocm_repository

    def filter(
        self,
        tgt_oci_registry: str,
    ) -> bool:
        return tgt_oci_registry in self._registries


class RegionsTarget(TargetBase):
    def __init__(
        self,
        registry: str,
        provider: str,
        regions: list[str],
        ocm_repository: str | None=None,
    ):
        self.registry = registry
        self.provider = provider
        self.regions = regions
        self.ocm_repository = ocm_repository

    def filter(
        self,
        tgt_oci_registry: str,
    ) -> bool:
        return tgt_oci_registry == self.registry
