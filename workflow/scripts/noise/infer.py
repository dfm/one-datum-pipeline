#!/usr/bin/env python
# -*- coding: utf-8 -*-

from typing import Tuple

import jax.numpy as jnp
import numpy as np
import numpyro
import numpyro.distributions as dist
from astropy.io import fits
from jax import random
from jax.config import config
from numpyro.infer import SVI, Trace_ELBO
from tqdm import tqdm

config.update("jax_enable_x64", True)


MIN_COLOR: float = 0.0
MAX_COLOR: float = 5.5
MIN_MAG: float = 4.5
MAX_MAG: float = 16.0


def setup_model() -> SVI:
    def model(num_transit, sample_variance):
        log_sigma0 = numpyro.sample("log_sigma0", dist.Normal(0.0, 10.0))
        log_dsigma = numpyro.sample(
            "log_dsigma",
            dist.Normal(0.0, 10.0),
            sample_shape=(len(sample_variance),),
        )
        sigma2 = jnp.exp(2 * log_sigma0) + jnp.exp(2 * log_dsigma)
        stat = sample_variance * (num_transit - 1)
        numpyro.sample(
            "obs", dist.Gamma(0.5 * (num_transit - 1), 0.5 / sigma2), obs=stat
        )

    def guide(num_transit, sample_variance):
        mu_log_sigma0 = numpyro.param(
            "mu_log_sigma0", 0.5 * np.log(np.median(sample_variance))
        )
        sigma_log_sigma0 = numpyro.param(
            "sigma_log_sigma0", 1.0, constraint=dist.constraints.positive
        )

        mu_log_dsigma = numpyro.param(
            "mu_log_dsigma", 0.5 * np.log(sample_variance)
        )
        sigma_log_dsigma = numpyro.param(
            "sigma_log_dsigma",
            np.ones_like(sample_variance),
            constraint=dist.constraints.positive,
        )

        numpyro.sample(
            "log_sigma0", dist.Normal(mu_log_sigma0, sigma_log_sigma0)
        )
        numpyro.sample(
            "log_dsigma", dist.Normal(mu_log_dsigma, sigma_log_dsigma)
        )

    optimizer = numpyro.optim.Adam(step_size=0.05)
    return SVI(model, guide, optimizer, loss=Trace_ELBO())


def load_data(
    data_path: str,
    *,
    min_nb_transits: int = 3,
    color_range: Tuple[float, float] = (MIN_COLOR, MAX_COLOR),
    mag_range: Tuple[float, float] = (MIN_MAG, MAX_MAG),
) -> fits.fitsrec.FITS_rec:
    print("Loading data...")
    with fits.open(data_path) as f:
        data = f[1].data

    m = np.isfinite(data["phot_g_mean_mag"])
    m &= np.isfinite(data["bp_rp"])
    m &= np.isfinite(data["dr2_radial_velocity_error"])

    m &= data["dr2_rv_nb_transits"] > min_nb_transits

    m &= color_range[0] < data["bp_rp"]
    m &= data["bp_rp"] < color_range[1]
    m &= mag_range[0] < data["phot_g_mean_mag"]
    m &= data["phot_g_mean_mag"] < mag_range[1]

    return data[m]


def fit_data(
    data: fits.fitsrec.FITS_rec,
    *,
    num_mag_bins: int,
    num_color_bins: int,
    color_range: Tuple[float, float] = (MIN_COLOR, MAX_COLOR),
    mag_range: Tuple[float, float] = (MIN_MAG, MAX_MAG),
    num_iter: int = 5,
    targets_per_fit: int = 1000,
    num_optim: int = 5000,
    seed: int = 11239,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    # Parse data
    num_transit = np.ascontiguousarray(
        data["dr2_rv_nb_transits"], dtype=np.int32
    )
    eps = np.ascontiguousarray(
        data["dr2_radial_velocity_error"], dtype=np.float32
    )
    sample_variance = 2 * num_transit * (eps ** 2 - 0.11 ** 2) / np.pi
    mag = np.ascontiguousarray(data["phot_g_mean_mag"], dtype=np.float32)
    color = np.ascontiguousarray(data["bp_rp"], dtype=np.float32)

    # Setup the JAX model
    svi = setup_model()

    # Setup the grid and allocate the memory
    mag_bins = np.linspace(mag_range[0], mag_range[1], num_mag_bins + 1)
    color_bins = np.linspace(
        color_range[0], color_range[1], num_color_bins + 1
    )
    mu = np.empty((len(mag_bins) - 1, len(color_bins) - 1, num_iter))
    sigma = np.empty_like(mu)
    count = np.empty((len(mag_bins) - 1, len(color_bins) - 1), dtype=np.int64)

    np.random.seed(seed)
    inds = np.arange(len(data))
    for n in tqdm(range(len(mag_bins) - 1), desc="magnitudes"):
        for m in tqdm(range(len(color_bins) - 1), desc="colors", leave=False):
            mask = mag_bins[n] <= mag
            mask &= mag <= mag_bins[n + 1]
            mask &= color_bins[m] <= color
            mask &= color <= color_bins[m + 1]

            count[n, m] = mask.sum()

            # For small amounts of data
            if count[n, m] <= targets_per_fit:
                if count[n, m] < 50:
                    mu[n, m, :] = np.nan
                    sigma[n, m, :] = np.nan
                    continue

                svi_result = svi.run(
                    random.PRNGKey(seed + n + m),
                    num_optim,
                    num_transit[mask],
                    sample_variance[mask],
                    progress_bar=False,
                )
                params = svi_result.params
                mu[n, m, :] = params["mu_log_sigma0"]
                sigma[n, m, :] = params["sigma_log_sigma0"]
                continue

            for k in tqdm(range(num_iter), desc="iterations", leave=False):
                masked_inds = np.random.choice(
                    inds[mask],
                    size=targets_per_fit,
                    replace=mask.sum() <= targets_per_fit,
                )
                svi_result = svi.run(
                    random.PRNGKey(seed + n + m + k),
                    num_optim,
                    num_transit[masked_inds],
                    sample_variance[masked_inds],
                    progress_bar=False,
                )
                params = svi_result.params
                mu[n, m, k] = params["mu_log_sigma0"]
                sigma[n, m, k] = params["sigma_log_sigma0"]

    return mu, sigma, count


if __name__ == "__main__":
    import argparse

    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", required=True, type=str)
    parser.add_argument("-o", "--output", required=True, type=str)
    parser.add_argument("-c", "--config", required=True, type=str)
    args = parser.parse_args()

    with open(args.config, "r") as f:
        config = yaml.load(f.read(), Loader=yaml.FullLoader)

    data = load_data(
        data_path=args.input,
        min_nb_transits=config["min_nb_transits"],
        color_range=(
            config["min_color"],
            config["max_color"],
        ),
        mag_range=(
            config["min_mag"],
            config["max_mag"],
        ),
    )

    mu, sigma, count = fit_data(
        data,
        num_mag_bins=config["num_mag"],
        num_color_bins=config["num_color"],
        color_range=(
            config["min_color"],
            config["max_color"],
        ),
        mag_range=(
            config["min_mag"],
            config["max_mag"],
        ),
        num_iter=config["num_iter"],
        targets_per_fit=config["targets_per_fit"],
        num_optim=config["num_optim"],
        seed=config["seed"],
    )

    # Save the results
    hdr = fits.Header()
    hdr["min_tra"] = config["min_nb_transits"]
    hdr["min_col"] = config["min_color"]
    hdr["max_col"] = config["max_color"]
    hdr["num_col"] = config["num_color"]
    hdr["min_mag"] = config["min_mag"]
    hdr["max_mag"] = config["max_mag"]
    hdr["num_mag"] = config["num_mag"]
    hdr["num_itr"] = config["num_iter"]
    hdr["num_per"] = config["targets_per_fit"]
    hdr["num_opt"] = config["num_optim"]
    hdr["seed"] = config["seed"]
    fits.HDUList(
        [
            fits.PrimaryHDU(header=hdr),
            fits.ImageHDU(mu),
            fits.ImageHDU(sigma),
            fits.ImageHDU(count),
        ]
    ).writeto(args.output, overwrite=True)
