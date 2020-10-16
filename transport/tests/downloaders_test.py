import processing.downloaders as downloaders


def test_oci_image_downloader(job, oci_img):
    img1 = oci_img()
    job1 = job(oci_img=img1)

    examinee = downloaders.Downloader()

    result = examinee.process(job1, 'file:path')

    assert result.container_image == img1
    assert result.download_request.source_ref == 'image_ref:1.2.3'
    assert result.download_request.target_file == 'file:path'
