import numpy as np

from ada.config import logger


def bernstein(n, k):
    """Bernstein polynomial."""
    from scipy.special._ufuncs import binom

    coeff = binom(n, k)

    def _bpoly(x):
        return coeff * x**k * (1 - x) ** (n - k)

    return _bpoly


def bezier(points, num=200):
    """Build Bezier curve from points."""
    N = len(points)
    t = np.linspace(0, 1, num=num)
    curve = np.zeros((num, 2))
    for ii in range(N):
        curve += np.outer(bernstein(N - 1, ii)(t), points[ii])
    return curve


def curve_fitting(in_data):
    from scipy.optimize import curve_fit

    xData = np.array(in_data[0])
    yData = np.array(in_data[1])

    # generate initial parameter values
    geneticParameters = generate_initial_parameters(xData, yData)

    # curve fit the test data
    fittedParameters, pcov = curve_fit(curve_f1, xData, yData, geneticParameters)

    logger.debug("Parameters", fittedParameters)

    modelPredictions = curve_f1(xData, *fittedParameters)

    absError = modelPredictions - yData

    SE = np.square(absError)  # squared errors
    MSE = np.mean(SE)  # mean squared errors
    RMSE = np.sqrt(MSE)  # Root Mean Squared Error, RMSE
    Rsquared = 1.0 - (np.var(absError) / np.var(yData))
    print("RMSE:", RMSE)
    print("R-squared:", Rsquared)
    print()
    return fittedParameters


def curve_f1(x, a, b, Offset):
    """A base function for use in curve fitting"""
    return 1.0 / (1.0 + np.exp(-a * (x - b))) + Offset


def curve_f2(x, a, b, c):
    """Another base function for use in curve fitting"""
    return a * np.exp(-b * x) + c


def curve_f3(x, a, b):
    """Yet another base function for use in curve fitting"""
    return a * np.exp(b * x)


def curve_f4(x, a, b, c):
    """And yet another base function for use in curve fitting"""
    return a * x**3 + b * x**2 + c * x


def sum_of_squared_error(parameter_tuple, *args):
    """function for genetic algorithm to minimize (sum of squared error)"""
    import warnings

    xData = args[0]
    yData = args[1]
    if xData is None:
        logger.error("Xdata is none. Returning None")
        return None
    warnings.filterwarnings("ignore")
    val = curve_f1(xData, *parameter_tuple)
    return np.sum((yData - val) ** 2.0)


def generate_initial_parameters(xData, yData):
    from scipy.optimize import differential_evolution

    # min and max used for bounds
    maxX = max(xData)
    minX = min(xData)
    maxY = max(yData)
    # minY = min(yData)

    parameterBounds = []
    parameterBounds.append([minX, maxX])  # seach bounds for a
    parameterBounds.append([minX, maxX])  # seach bounds for b
    parameterBounds.append([0.0, maxY])  # seach bounds for Offset

    # "seed" the numpy random number generator for repeatable results
    result = differential_evolution(sum_of_squared_error, parameterBounds, args=[xData, yData], seed=3)
    return result.x
