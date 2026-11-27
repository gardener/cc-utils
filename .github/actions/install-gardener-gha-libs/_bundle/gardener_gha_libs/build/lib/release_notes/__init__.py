'''
Release Notes Collector

Collects release notes for a given version from a ComponentDescriptor.

For a new patch release, all commits between the last patch tag up to the
current patch tag are included in the release notes. For a new minor release,
all commits between the last minor release (e.g. 1.63.0) up to the current
version are included and all commits before the last minor are removed.  It
doesn't matter on which branch the release tags are created, as long as they
share an ancestor.

This package is a re-implementation of the current release note collector
(https://github.com/gardener/cc-utils/tree/master/github/release_notes) to
avoid code freezes, which currently occur because in the current version you
have to release on a branch, while in the re-implementation you can release on
any branch.

See https://github.com/gardener/cc-utils/blob/master/doc/release_notes.rst for
more info.
'''
