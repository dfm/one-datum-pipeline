import yaml
from snakemake.utils import validate

validate(config, "../schemas/config.schema.yaml")

with open(config["noise_config_file"], "r") as f:
    noise_config = yaml.load(f.read(), Loader=yaml.FullLoader)
validate(noise_config, "../schemas/noise.schema.yaml")

with open(config["sims_config_file"], "r") as f:
    sims_config = yaml.load(f.read(), Loader=yaml.FullLoader)
validate(sims_config, "../schemas/simulations.schema.yaml")


def get_filename_for_dataset(filename):
    return get_results_filename(
        f"{config['dataset_name']}/{filename}"
    )


def get_remote_filename(filename):
    return f"{config.get('remote_basedir', 'remote')}/{filename}"


def get_results_filename(filename):
    return f"{config.get('results_basedir', 'results')}/{filename}"


def get_log_filename(filename):
    basedir = config.get('log_basedir', f"{config.get('results_basedir', 'results')}/logs")
    return f"{basedir}/{filename}"
