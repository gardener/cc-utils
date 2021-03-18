import concourse.replicator


def replicate_pipelines():
    replicator = concourse.replicator.replicate_pipelines()
    print(replicator)
