import numpy as np


# region Curve Fitting Algorithms
def nmad(e, p):
    r"""
    Normalized Mean Absolute Difference (NMAD)

    The Normalized Mean Absolute Difference (NMAD) fitness value represents the average error in percent between the
    experimental data and the model predictions. The NMAD value is defined by:

        NMAD = 100*(\|e-p|)max(\|e|,\|p|)

    :param e: Is the vector of experimental stress values (or if strain control,
              the vector of experimental strain values)
    :param p: Is the vector of predicted stress values
    :return:
    """
    return 100 * (e - p) / max(e, p)


def msd(e, p):
    """
    Mean Square Difference (MSD)
    The Mean Square Difference (MSD) is defined by:

        MSD=1n∑i=1n(ei–pi)2

    where:

        ei is an experimental stress value (or strain value if stress control)
        pi is a predicted stress value (of strain value if stress control)
        n is the total number of points

    :return:
    """
    n = len(e)
    return (1 / n) * sum([(ei - pi) ** 2 for ei, pi in zip(e, p)])


def coeff_det(e, p):
    """
    Coefficient of Determination
    The Coefficient of Determination (R2) is defined by:

        R2=1−∑i(ei–pi)2(ei–em)2

    where

        ei is an experimental stress value (or strain value if stress control)
        pi is a predicted stress value (or strain value if stress control)
        em is the mean of the experimental values

    :return:
    """
    em = sum(e) / len(e)

    return 1 - sum([(ei - pi) ** 2 for ei, pi in zip(e, p)]) / sum([(ei - em) ** 2 for ei in e])


# endregion

# region Coefficient conversions
def v_from_mu_kappa(mu, kappa):
    return (3 * kappa - 2 * mu) / (6 * kappa + 2 * mu)


def lambda_from_mu_kappa(mu, kappa):
    pass


def E_from_mu_kappa(mu, kappa):
    """
    Young's Modulus from known shear and bulk modulus'

    :param mu: Shear Modulus
    :param kappa: Bulk Modulus
    :return:
    """
    return 9 * kappa * mu / (3 * kappa + mu)


def S_from_sig(e, sig):
    """
    Convert true stress to engineering stress

    :param e: engineering strain
    :param sig: true stress
    :return: engineering stress
    """
    return np.array([s / (1 + e_) for e_, s in zip(e, sig)])


def e_from_eps(eps):
    """
    Convert true strain to engineering strain

    :param eps: true strain
    :return: engineering strain
    """
    return np.exp(eps) - 1


# endregion
