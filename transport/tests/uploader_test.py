import processing.uploaders as uploaders


PREFIX_UPLOADERS = [
    {
        'context_url': 'registry.local:5000/context-dir',
        'prefix': 'registry.local:5000',
        'mangle': True,
        'expected_target_ref': 'registry.local:5000/registry-source_local:1.2.3',
    },
    {
        'context_url': 'registry.local/context-dir',
        'prefix': 'registry.local',
        'mangle': False,
        'expected_target_ref': 'registry.local/registry-source.local:1.2.3',
    },
]


def test_prefix_uploader(job, oci_img):
    img1 = oci_img(name='image_name', version='1.2.3', ref='registry-source.local:1.2.3')
    job1 = job(oci_img=img1)

    results = []
    for uploader in PREFIX_UPLOADERS:
        examinee = uploaders.PrefixUploader(
            context_url=uploader['context_url'],
            prefix=uploader['prefix'],
            mangle=uploader['mangle'],
        )

        result = examinee.process(job1, target_as_source=False)
        assert result.upload_request.target_ref == uploader['expected_target_ref']
        results.append(result)

    return results


def test_tag_suffix_uploader(job, oci_img):
    for j in test_prefix_uploader(job, oci_img):
        examinee = uploaders.TagSuffixUploader(
            suffix='mod1',
            separator='-',
        )

        result = examinee.process(j, target_as_source=True)
        assert result.upload_request.target_ref == j.upload_request.target_ref + '-mod1'
