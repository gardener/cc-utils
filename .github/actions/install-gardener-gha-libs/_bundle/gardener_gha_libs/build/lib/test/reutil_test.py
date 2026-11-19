from reutil import re_filter


def test_re_filter_empty_filter_matches_everything():
    empty_filter = re_filter() # empty filter matches nothing

    assert empty_filter('foo') is True

    empty_filter_with_trans = re_filter(value_transformation=str)

    assert empty_filter_with_trans('foo') is True


def test_re_filter_include_matches():
    include_filter = re_filter(include_regexes=('^aaa.*', '^bbb'))

    assert include_filter('aaa')
    assert include_filter('bbb')

    assert not include_filter('ccc')
    assert not include_filter('bbbb') # require full match


def test_re_filter_exclude():
    exclude_filter = re_filter(exclude_regexes=('^aaa.*', '^bbb'))

    assert not exclude_filter('aaa')
    assert not exclude_filter('bbb')
    assert exclude_filter('fooaaa')
    assert exclude_filter('bbbb') # require full match
    assert exclude_filter('ccc')

    exclude_and_include = re_filter(include_regexes=('^a',), exclude_regexes=('^a',))

    # exclusion has precedence
    assert not exclude_and_include('a')
