import string

from abc import abstractmethod

from model.base import ModelValidationError

def ensure_not_none(value):
    if value is None:
        raise ValueError('must not be none')
    return value

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


class PipelineStep(ModelBase):
    def __init__(self, name, is_synthetic=False, *args, **kwargs):
        self.name = name
        self.is_synthetic = is_synthetic
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

    def image(self):
        return self.raw.get('image', None)

    def registry(self):
        return self.raw.get('registry', None)

    def execute(self):
        # by default, run an executable named as the step
        return self.raw.get('execute', self.name)

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
                fail('forbidden character in image reference: ' + str(image_reference))
            if not ':' in image_reference:
                fail('image reference must contain colon charater:' + str(image_reference))

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

