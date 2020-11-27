def normalise_image_reference(image_reference: str):
  if not isinstance(image_reference, str):
    raise ValueError(image_reference)

  if '@' in image_reference:
    return image_reference

  parts = image_reference.split('/')

  left_part = parts[0]
  # heuristically check if we have a (potentially) valid hostname
  if '.' not in left_part.split(':')[0]:
    # insert 'library' if only image name was given
    if len(parts) == 1:
      parts.insert(0, 'library')

    # probably, the first part is not a hostname; inject default registry host
    parts.insert(0, 'registry-1.docker.io')

  # of course, docker.io gets special handling
  if parts[0] == 'docker.io':
      parts[0] = 'registry-1.docker.io'

  return '/'.join(parts)


def urljoin(*parts):
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    last = parts[-1]
    middle = parts[1:-1]

    first = first.rstrip('/')
    middle = list(map(lambda s: s.strip('/'), middle))
    last = last.lstrip('/')

    return '/'.join([first] + middle + [last])
