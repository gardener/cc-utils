import dataclasses
import functools
import json
import logging
import os
import os.path
import pkgutil
import sys
import threading
import typing
import urllib.parse

import dacite
import deprecated
import github3.exceptions
import yaml

import ci.log
import ctx

from model.base import (
    ConfigElementNotFoundError,
    ModelValidationError,
    NamedModelElement,
)
from ci.util import (
    existing_dir,
    not_empty,
    not_none,
    parse_yaml_file,
)

dc = dataclasses.dataclass
empty_list = dataclasses.field(default_factory=list)
empty_tuple = dataclasses.field(default_factory=tuple)

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)

'''
Configuration model and retrieval handling.

Users of this module will most likely want to create an instance of `ConfigFactory` and use it
to create `ConfigurationSet` instances.

Configuration sets are factories themselves, that are backed with a configuration source.
They create concrete configuration instances. While technically modifiable, all configuration
instances should not be altered by users. Configuration objects should usually not be
instantiated by users of this module.
'''


@dc(frozen=True)
class BaseConfigSetCfg:
    name: str


@dc(frozen=True)
class CfgTypeSrc: # just a marker class
    pass


@dc(frozen=True)
class LocalFileCfgSrc(CfgTypeSrc):
    file: str


@dc(frozen=True)
class GithubRepoFileSrc(CfgTypeSrc):
    repository_url: str
    relpath: str


@dc(frozen=True)
class ConfigTypeModel:
    factory_method: typing.Optional[str]
    cfg_type_name: str
    type: str


@dc(frozen=True)
class ConfigType:
    '''
    represents a configuration type (used for serialisation and deserialisation)
    '''
    model: ConfigTypeModel
    src: typing.Tuple[typing.Union[LocalFileCfgSrc, GithubRepoFileSrc], ...] = empty_tuple

    def sources(self):
        return self.src

    def factory_method(self):
        return self.model.factory_method

    def cfg_type_name(self):
        return self.model.cfg_type_name

    def cfg_type(self):
        return self.model.type


class ConfigFactory:
    '''Creates configuration model element instances from the underlying configuration source

    Configuration elements are organised in a two-level hierarchy: Configuration type
    (named cfg_type by convention) which specifies a configuration schema (and semantics) and
    Configuration element name (named cfg_name or element_name by convention).

    Configuration model elements may be retrieved through one of two methods:

        - via the generic `_cfg_element(cfg_type_name, cfg_name)`
        - via a "factory method" (if defined in cfg_type) - example: `github(cfg_name)`

    There is a special configuration type named `ConfigurationSet`, which is used to group
    sets of configuration elements. Configuration sets expose an API equivalent to ConfigFactory.
    '''

    CFG_TYPES = 'cfg_types'
    _cfg_type_cache = {} # <name>:<type>

    @staticmethod
    def _parse_local_file(cfg_dir: str, cfg_src: LocalFileCfgSrc):
        cfg_file = cfg_src.file
        return parse_yaml_file(os.path.join(cfg_dir, cfg_file))

    @staticmethod
    def _parse_repo_file(
        cfg_src: GithubRepoFileSrc,
        lookup_cfg_factory,
    ):
        import ccc.github
        repo_url = cfg_src.repository_url
        if not '://' in repo_url:
            repo_url = 'https://' + repo_url
        repo_url = urllib.parse.urlparse(repo_url)

        # shortcut lookup if local repo-mapping was configured
        if ctx.cfg and ctx.cfg.ctx and (repo_mappings := ctx.cfg.ctx.github_repo_mappings):
            for repo_mapping in repo_mappings:
                url = repo_mapping.repo_url
                if not '://' in url:
                    url = 'https://' + url
                url = urllib.parse.urlparse(url)
                if not url == repo_url:
                    continue
                fpath = os.path.join(repo_mapping.path, cfg_src.relpath)
                with open(fpath) as f:
                    print(f'cfg loaded from local dir {fpath=}')
                    return yaml.safe_load(f)

        if not lookup_cfg_factory:
            raise RuntimeError('cannot resolve non-local cfg w/o bootstrap-cfg-factory')

        gh_api = ccc.github.github_api(
            ccc.github.github_cfg_for_repo_url(
                repo_url,
                cfg_factory=lookup_cfg_factory,
            ),
            cfg_factory=lookup_cfg_factory,
        )
        org, repo = repo_url.path.strip('/').split('/')
        gh_repo = gh_api.repository(org, repo)

        try:
            file_contents = gh_repo.file_contents(
                path=cfg_src.relpath,
                ref=gh_repo.default_branch,
            ).decoded.decode('utf-8')

        except github3.exceptions.NotFoundError:
            logger.error(
                f"Unable to access file '{cfg_src.relpath}' in repository '{repo_url.path}'"
            )
            raise

        return yaml.safe_load(file_contents)

    @staticmethod
    def from_cfg_dir(
        cfg_dir: str,
        cfg_types_file='config_types.yaml',
        disable_cfg_element_lookup=False,
    ):
        if not cfg_dir:
            raise ValueError('cfg dir must not be None')

        cfg_dir = os.path.abspath(cfg_dir)
        if not os.path.isdir(cfg_dir):
            raise ValueError(f'{cfg_dir=} is not a directory')

        bootstrap_cfg_factory = ConfigFactory._from_cfg_dir(
            cfg_dir=cfg_dir,
            cfg_types_file=cfg_types_file,
            cfg_src_types=(LocalFileCfgSrc,),
            disable_cfg_element_lookup=False,
        )

        if disable_cfg_element_lookup:
            return bootstrap_cfg_factory

        return ConfigFactory._from_cfg_dir(
            cfg_dir=cfg_dir,
            cfg_types_file=cfg_types_file,
            cfg_src_types=None, # all
            lookup_cfg_factory=bootstrap_cfg_factory,
            disable_cfg_element_lookup=disable_cfg_element_lookup,
        )

    @staticmethod
    def _from_cfg_dir(
        cfg_dir: str,
        disable_cfg_element_lookup: bool,
        cfg_types_file='config_types.yaml',
        cfg_src_types=None,
        lookup_cfg_factory=None,
    ):
        cfg_dir = existing_dir(os.path.abspath(cfg_dir))
        cfg_types_dict = parse_yaml_file(os.path.join(cfg_dir, cfg_types_file))
        raw = {}

        raw[ConfigFactory.CFG_TYPES] = cfg_types_dict

        def retrieve_cfg(cfg_type):
            cfg_dict = {}

            for cfg_src in cfg_type.sources():
                if cfg_src_types and type(cfg_src) not in cfg_src_types:
                    continue

                if isinstance(cfg_src, LocalFileCfgSrc):
                    parsed_cfg = ConfigFactory._parse_local_file(
                        cfg_dir=cfg_dir,
                        cfg_src=cfg_src,
                    )
                elif isinstance(cfg_src, GithubRepoFileSrc):
                    if disable_cfg_element_lookup:
                        continue
                    parsed_cfg = ConfigFactory._parse_repo_file(
                        cfg_src=cfg_src,
                        lookup_cfg_factory=lookup_cfg_factory,
                    )
                else:
                    raise NotImplementedError(cfg_src)

                for k,v in parsed_cfg.items():
                    if k in cfg_dict and cfg_dict[k] != v:
                        raise ValueError(f'conflicting definition for {k=}')
                    cfg_dict[k] = v

            return cfg_dict

        return ConfigFactory(
            raw_dict=raw,
            retrieve_cfg=retrieve_cfg,
        )

    @staticmethod
    def from_dict(raw_dict: dict):
        raw = not_none(raw_dict)

        return ConfigFactory(raw_dict=raw)

    def __init__(
        self,
        raw_dict: dict,
        retrieve_cfg: typing.Callable[[ConfigType], dict]=None,
    ):
        self.raw = not_none(raw_dict)
        if self.CFG_TYPES not in self.raw:
            raise ValueError(f'missing required attribute: {self.CFG_TYPES}')
        self.retrieve_cfg = retrieve_cfg

    def _retrieve_cfg_elements(self, cfg_type_name: str):
        if not cfg_type_name in self.raw:
            cfg_type = self._cfg_type(cfg_type_name=cfg_type_name)
            if self.retrieve_cfg:
                cfg_dict = self.retrieve_cfg(cfg_type)
            else:
                cfg_dict = {}
                # XXX hacky: use empty-dict if there is no retrieval-callable

            self.raw[cfg_type_name] = cfg_dict

    @deprecated.deprecated
    def _configs(self, cfg_name: str):
        '''
        returns all known cfg-element-names
        '''
        self._retrieve_cfg_elements(cfg_type_name=cfg_name)
        return self.raw[cfg_name]

    @functools.lru_cache
    def _cfg_types(self):
        return {
            cfg.cfg_type_name(): cfg for
            cfg in (
                dacite.from_dict(
                    data_class=ConfigType,
                    data=cfg_dict,
                    config=dacite.Config(
                        cast=[tuple],
                    ),
                ) for cfg_dict in self.raw[self.CFG_TYPES].values()
            )
        }

    def _cfg_type(self, cfg_type_name: str):
        self._ensure_type_is_known(cfg_type_name=cfg_type_name)
        return self._cfg_types().get(cfg_type_name)

    def _cfg_types_raw(self):
        return self.raw[self.CFG_TYPES]

    def cfg_set(self, cfg_name: str) -> 'ConfigurationSet':
        '''
        returns a new `ConfigurationSet` instance for the specified config name backed by the
        configured configuration source.
        '''
        configs_dict = self._configs('cfg_set')

        if cfg_name not in configs_dict:
            raise ValueError('no cfg named {c} in {cs}'.format(
                c=cfg_name,
                cs=', '.join(configs_dict.keys())
            )
            )
        return ConfigurationSet(
            cfg_factory=self,
            cfg_name=cfg_name,
            raw_dict=configs_dict[cfg_name]
        )

    def _cfg_element(self, cfg_type_name: str, cfg_name: str):
        cfg_type = self._cfg_type(cfg_type_name=cfg_type_name)

        # retrieve model class c'tor - search module and sub-modules
        # TODO: switch to fully-qualified type names
        own_module = sys.modules[__name__]

        submodule_names = [
            own_module.__name__ + '.' + m.name
            for m in pkgutil.iter_modules(own_module.__path__)
        ]
        for module_name in [__name__] + submodule_names:
            submodule_name = module_name.split('.')[-1]
            if module_name != __name__:
                module = getattr(__import__(module_name), submodule_name)
            else:
                module = sys.modules[submodule_name]

            # skip if module does not define our type
            if not hasattr(module, cfg_type.cfg_type()):
                continue

            # if type is defined, validate
            element_type = getattr(module, cfg_type.cfg_type())
            if not type(element_type) == type:
                raise ValueError()
            # found it (write to cache as part of crazy workaround for kaniko)
            self._cfg_type_cache[cfg_type_name] = element_type
            break
        else:
            # workaround for kaniko, which will purge our poor modules on multi-stage-builds
            if cfg_type_name in self._cfg_type_cache:
                element_type = self._cfg_type_cache[cfg_type_name]
            else:
                print(f'{self._cfg_type_cache=}')
                raise ValueError(f'failed to find cfg type: {cfg_type.cfg_type()=}')

        # for now, let's assume all of our model element types are subtypes of NamedModelElement
        # (with the exception of ConfigurationSet)
        configs = self._configs(cfg_type.cfg_type_name())
        if cfg_name not in configs:
            known_cfg_names = ', '.join(configs.keys())

            raise ConfigElementNotFoundError(
                f'cfg-factory: no such cfg-element: {cfg_name=} {cfg_type.cfg_type_name()=} '
                f'{known_cfg_names=}'
            )
        kwargs = {'raw_dict': configs[cfg_name]}

        if element_type == ConfigurationSet:
            kwargs.update({'cfg_name': cfg_name, 'cfg_factory': self})
        else:
            kwargs['name'] = cfg_name
            kwargs['type_name'] = cfg_type.cfg_type_name()

        element_instance = element_type(**kwargs)

        try:
            element_instance.validate()
        except ModelValidationError as mve:
            logger.warning(
                f"validation error for config '{cfg_name}' of type '{element_type.__name__}' "
                f"- ignored: {mve}"
            )

        return element_instance

    def _ensure_type_is_known(self, cfg_type_name: str):
        if cfg_type_name not in (known_types := self._cfg_types()):
            raise ValueError("Unknown config type '{c}'. Known types: {k}".format(
                c=cfg_type_name,
                k=', '.join(known_types.keys()),
            ))

    def _cfg_elements(self, cfg_type_name: str):
        '''Returns all cfg_elements for the given cfg_type.

        Parameters
        ----------
        cfg_type_name: str
            The name of the cfg_type whose instances should be retrieved.

        Yields
        -------
        NamedModelElement
            Instance of the given cfg_type.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)

        self._retrieve_cfg_elements(cfg_type_name=cfg_type_name)

        self._ensure_type_is_known(cfg_type_name=cfg_type_name)

        for element_name in self._cfg_element_names(cfg_type_name):
            yield self._cfg_element(cfg_type_name, element_name)

    def _cfg_element_names(self, cfg_type_name: str):
        '''Returns cfg-elements of the given cfg_type

        Parameters
        ----------
        cfg_type_name: str
            The cfg type name

        Returns
        -------
        Iterable[str]
            Contains the names of all cfg-elements of the given cfg_type known to this ConfigFactory.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)
        self._retrieve_cfg_elements(cfg_type_name=cfg_type_name)

        self._ensure_type_is_known(cfg_type_name=cfg_type_name)
        if cfg_type_name in self.raw:
            return set(self.raw[cfg_type_name].keys())
        else:
            return set()

    def __dir__(self):
        # prepend factory methods (improve REPL-shell experience)
        for cfg_type in self._cfg_types().values():
            if (factory_method := cfg_type.factory_method()):
                yield factory_method

            # also include methods for NamedModelElement-derived types
            elif cfg_type.cfg_type() == NamedModelElement.__name__:
                yield cfg_type.cfg_type_name()

        yield from super().__dir__()

    def __getattr__(self, cfg_type_name):
        for cfg_type in self._cfg_types().values():
            if cfg_type.factory_method() == cfg_type_name:
                return functools.partial(self._cfg_element, cfg_type_name)

            if cfg_type.cfg_type() == NamedModelElement.__name__:
                return functools.partial(self._cfg_element, cfg_type_name)

        raise AttributeError(cfg_type_name)

    def _serialise(self):
        cfg_types = self._cfg_types()

        serialised_elements = {
            cfg_name: self.retrieve_cfg(cfg_type) for cfg_name, cfg_type in cfg_types.items()
        }
        serialised_elements[ConfigFactory.CFG_TYPES] = self._cfg_types_raw()

        return json.dumps(serialised_elements, indent=2)


class ConfigSetSerialiser:
    def __init__(self, cfg_sets: 'ConfigurationSet', cfg_factory: ConfigFactory):
        self.cfg_sets = not_none(cfg_sets)
        self.cfg_factory = not_none(cfg_factory)

    def serialise(self, output_format='json'):
        if not output_format == 'json':
            raise ValueError('not implemented')
        if len(self.cfg_sets) < 1:
            return '{}' # early exit for empty cfg_sets-set

        cfg_types = self.cfg_factory._cfg_types()
        # collect all cfg_names (<cfg-type>:[cfg-name])
        cfg_mappings = {}
        for cfg_mapping in [cfg_set._cfg_mappings() for cfg_set in self.cfg_sets]:
            for cfg_type_name, cfg_set_mapping in cfg_mapping:
                cfg_type = cfg_types[cfg_type_name]
                if cfg_type not in cfg_mappings:
                    cfg_mappings[cfg_type] = set()
                cfg_mappings[cfg_type].update(cfg_set_mapping['config_names'])

        # assumption: all cfg_sets share the same cfg_factory / all cfg_names are organized in one
        # global, flat namespace
        def serialise_element(cfg_type, cfg_names):
            elem_cfgs = {}

            for cfg_name in cfg_names:
                element = self.cfg_factory._cfg_element(cfg_type.cfg_type_name(), cfg_name)
                elem_cfgs[element.name()] = element.raw

            return (cfg_type.cfg_type_name(), elem_cfgs)

        serialised_elements = dict([serialise_element(t, n) for t, n in cfg_mappings.items()])

        # store cfg_set
        serialised_elements['cfg_set'] = {cfg.name(): cfg.raw for cfg in self.cfg_sets}

        # store cfg_types metadata (TODO: patch source attributes)
        serialised_elements[ConfigFactory.CFG_TYPES] = self.cfg_factory._cfg_types_raw()

        return json.dumps(serialised_elements, indent=2)


class ConfigurationSet(NamedModelElement):
    '''
    Represents a set of corresponding configuration. Instances are created by `ConfigFactory`.
    `ConfigurationSet` is itself a factory, offering a set of factory methods which create
    concrete configuration objects based on the backing configuration source.

    Note: This class is not thread safe until base_cfg_sets are resolved (_resolve_base_cfg_sets).

    Not intended to be instantiated by users of this module
    '''

    def __init__(self, cfg_factory, cfg_name, *args, **kwargs):
        self.resolved_base_cfg_sets = False
        self.cfg_factory: ConfigFactory = not_none(cfg_factory)
        self._raw_lock = threading.Lock()
        super().__init__(
            name=cfg_name,
            type_name='cfg_set',
            *args,
            **kwargs,
        )

        # normalise cfg mappings
        for cfg_type_name, entry in self.raw.items():
            if type(entry) == dict:
                entry = {
                    'config_names': entry['config_names'],
                    'default': entry.get('default', None)
                }
            elif type(entry) == str:
                entry = {'config_names': [entry], 'default': entry}

            self.raw[cfg_type_name] = entry

    def _base_cfgs(self):
        with self._raw_lock:
            return tuple(
                BaseConfigSetCfg(**cfg) for cfg in self.raw.get('_base_cfgs', {})
            )

    def _raw(self):
        return{
            k: v for k, v in self.raw.items() if not k == '_base_cfgs'
        }

    def _resolve_base_cfg_sets(self):
        if self.resolved_base_cfg_sets:
            return
        for base_cfg in self._base_cfgs():
            cfg_set = self.cfg_factory.cfg_set(cfg_name=base_cfg.name)
            self._merge_cfg_set(cfg_set=cfg_set, base_cfg=base_cfg)

        self.resolved_base_cfg_sets = True

    def _merge_cfg_set(self, cfg_set: 'ConfigurationSet', base_cfg: BaseConfigSetCfg):
        with self._raw_lock:
            cfg_set_dict = cfg_set._raw()

        for cfg_type_name, elements in cfg_set_dict.items():
            if not self.raw.get(cfg_type_name, {}):
                self.raw[cfg_type_name] = elements
            else:
                with self._raw_lock:
                    our_element_names = self._raw()[cfg_type_name]['config_names']

                # only add elements once
                other_element_names = [
                    cfg_name for cfg_name in elements.get('config_names')
                    if not cfg_name in our_element_names
                ]

                self.raw[cfg_type_name]['config_names'].extend(other_element_names)

                # use the first default from referenced based cfg sets if there is none
                if not self._raw()[cfg_type_name].get('default', None):
                    self.raw[cfg_type_name]['default'] = elements.get('default', None)

    def _optional_attributes(self):
        return {
            cfg_type_name for cfg_type_name in self.cfg_factory._cfg_types_raw()
        }

    def _cfg_mappings(self):
        self._resolve_base_cfg_sets()
        return self._raw().items()

    def _cfg_element(self, cfg_type_name: str, cfg_name=None):
        self.cfg_factory._ensure_type_is_known(cfg_type_name=cfg_type_name)

        cfg_name = self._default_name(cfg_type_name=cfg_type_name, cfg_name=cfg_name)

        return self.cfg_factory._cfg_element(
            cfg_type_name=cfg_type_name,
            cfg_name=cfg_name,
        )

    def _cfg_elements(self, cfg_type_name: str):
        '''Returns all container cfg elements of the given cfg_type

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)

        for element_name in self._cfg_element_names(cfg_type_name):
            yield self._cfg_element(cfg_type_name, element_name)

    def _cfg_element_names(self, cfg_type_name: str):
        '''Returns all container cfg element names

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)
        self._resolve_base_cfg_sets()
        # ask factory for all known names. This ensures that the existance of the type is checked.
        all_cfg_element_names = self.cfg_factory._cfg_element_names(cfg_type_name=cfg_type_name)

        if cfg_type_name in self._raw().keys():
            our_cfg_names = set(self._raw()[cfg_type_name]['config_names'])
            return (all_cfg_element_names & our_cfg_names) | our_cfg_names
        else:
            return set()

    def _default_name(self, cfg_type_name, cfg_name=None):
        if cfg_name:
            return cfg_name

        if not cfg_type_name in self._raw():
            self._resolve_base_cfg_sets()

        if not cfg_type_name in self._raw():
            raise ValueError(
                f'{self.name()=}: {cfg_type_name=} is unknown - known: {self._raw().keys()}'
            )

        cfg_name = self._raw()[cfg_type_name].get('default', None)
        if not cfg_name:
            self._resolve_base_cfg_sets()
            if not (cfg_name := self._raw()[cfg_type_name].get('default', None)):
                raise ValueError(f'No default for {cfg_type_name=}')
        return cfg_name

    def __getattr__(self, cfg_type_name):
        if not hasattr(self.cfg_factory, cfg_type_name):
            raise AttributeError(cfg_type_name)
        factory_method = getattr(self.cfg_factory, cfg_type_name)

        if not callable(factory_method):
            raise AttributeError(cfg_type_name)

        def get_default_element(cfg_name=None):
            if not cfg_name:
                cfg_name = self._default_name(cfg_type_name=cfg_type_name)

            return factory_method(cfg_name=cfg_name)
        return get_default_element

    def validate(self):
        cfg_types = self.cfg_factory._cfg_types()
        for cfg_type_name in cfg_types:
            for element in self._cfg_elements(cfg_type_name):
                element.validate()


def cluster_domain_from_kubernetes_config(cfg_factory, kubernetes_config_name: str):
    kubernetes_cfg = cfg_factory.kubernetes(kubernetes_config_name)
    if not (cluster_domain := kubernetes_cfg.cluster_domain()):
        raise ValueError(f'No cluster domain configured in {kubernetes_config_name=}')
    return cluster_domain
