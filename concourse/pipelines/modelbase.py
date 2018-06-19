import string
import shlex

from abc import abstractmethod
from enum import Enum

from model.base import ModelValidationError

def ensure_not_none(value):
    if value is None:
        raise ValueError('must not be none')
    return value
# export shorter alias
not_none = ensure_not_none

class ModelBase(object):
    def __init__(self, raw_dict: dict):
        ensure_not_none(raw_dict)
        self.custom_init(raw_dict)
        self.raw = raw_dict

    def validate(self):
        pass

    def custom_init(self, raw_dict: dict):
        pass


class Trait(object): # todo: base on NamedModelBase
    def __init__(self, name: str, variant_name: str, raw_dict: dict):
        self.name = ensure_not_none(name)
        self.variant_name = ensure_not_none(variant_name)
        self.raw = ensure_not_none(raw_dict)

    @abstractmethod
    def transformer(self):
        raise NotImplementedError()

    def __str__(self):
        return 'Trait: {n}'.format(n=self.name)


class TraitTransformer(object):
    def __init__(self, name: str):
        self.name = ensure_not_none(name)

    def inject_steps(self):
        return []

    def dependencies(self):
        return {self.name}

    @abstractmethod
    def process_pipeline_args(self, pipeline_args: 'PipelineArgs'):
        raise NotImplementedError()

class ScriptType(Enum):
    BOURNE_SHELL = 0
    PYTHON3 = 1


class PipelineStep(ModelBase):
    def __init__(self, name, is_synthetic=False, script_type=ScriptType.BOURNE_SHELL, *args, **kwargs):
        self.name = name
        self.is_synthetic = is_synthetic
        self._script_type = script_type
        self._outputs_dict = {}
        self._inputs_dict = {}
        super().__init__(*args, **kwargs)

    def custom_init(self, raw_dict: dict):
        if not 'depends' in raw_dict:
            raw_dict['depends'] = {self.name} # toposort lib requires non-empty dependecy sets
        else:
            raw_dict['depends'] = set(raw_dict['depends'])
        if raw_dict.get('output_dir', None):
            name = raw_dict['output_dir']
            self.add_output(name + '_path', name + '_path')

    def script_type(self) -> ScriptType:
        '''
        returns the step's "script type". The script type specifies the execution environment in which
        the script payload is run (script payloads are hard-coded in pipeline templates).
        '''
        return self._script_type

    def image(self):
        return self.raw.get('image', None)

    def command_string(self):
        '''Calculate and return the combined command-string consisting of the executable and all arguments.

        If no arguments are specified, this method returns the shell-escaped executable as given by
        `self.execute()`. If there is one argument specified, it is assumed to be properly shell-escaped and
        the space-seperated concatenation of the shell-escaped executable and the argument is returned.
        Finally, if a list of arguments is configured for the step, a space-seperated concatenation of the
        shell-escaped executable and each argument (individually shell-escaped) is returned.

        Returns
        ------
        str
            A properly shell-escaped string consisting of the executable followed by all arguments.
        '''
        arguments = self.raw.get('arguments', None)
        shell_escaped_executable = shlex.quote(self.execute())

        if arguments is None:
            shell_escaped_arguments = []
        elif not isinstance(arguments, list):
            shell_escaped_arguments = [str(arguments)]
        else:
            shell_escaped_arguments = [shlex.quote(str(argument)) for argument in arguments]

        return ' '.join([shell_escaped_executable] + shell_escaped_arguments)

    def registry(self):
        return self.raw.get('registry', None)

    def execute(self, executable_path_prefix: str=''):
        # by default, run an executable named as the step
        return os.path.join(executable_path_prefix, self.raw.get('execute', self.name))

    def output_dir(self):
        if not 'output_dir' in self.raw:
            return None

        # an optional attribute specifying the "output directory"
        # due to "historical" reasons, append '-path' suffix
        return self.raw.get('output_dir') + '_path'

    def output(self, name):
        return self.outputs()[name]

    def outputs(self):
        return self._outputs_dict

    def add_output(self, name, variable_name):
        if name in self._outputs_dict:
            raise ValueError('output already exists: ' + str(name))
        self._outputs_dict[name] = variable_name

    def inputs(self):
        return self._inputs_dict

    def input(self, name):
        return self.inputs()[name]

    def add_input(self, name, variable_name):
        if name in self._inputs_dict:
            raise ValueError('input already exists: ' + str(name))
        self._inputs_dict[name] = variable_name

    def publish_repository_names(self):
        return self.raw.get('publish_to', [])

    def _add_dependency(self, step: 'PipelineStep'):
        self.raw['depends'].add(step.name)

    def depends(self):
        return set(self.raw['depends'])

    def validate(self):
        if self.image():
            image_reference = self.image()
            # image must be a valid docker image reference
            allowed_characters = string.ascii_letters + string.digits +'.-_/:'
            if not all(map(lambda c: c in allowed_characters, image_reference)):
                raise ModelValidationError('forbidden character in image reference: ' + str(image_reference))
            if not ':' in image_reference:
                raise ModelValidationError('image reference must contain colon charater:' + str(image_reference))
        if isinstance(self.raw.get('arguments', None), dict):
            raise ModelValidationError('step arguments must be a scalar value or a list')

    def __str__(self):
        descr = 'PipelineStep {n} - depends: {d}, inputs: {i}, outputs: {o}'.format(
            n=self.name,
            d=self.depends(),
            i=self.inputs(),
            o=self.outputs(),
        )
        return descr


def normalise_to_dict(dictish):
    if type(dictish) == str:
        return {dictish: {}}
    if type(dictish) == list:
        values = []
        for v in dictish:
            if type(v) == dict:
                values.append(v.popitem())
            else:
                values.append((v, {}))
        return dict(values)
    return dictish


def fail(msg):
    raise ModelValidationError(msg)

def select_attr(name):
    return lambda o: o.name

