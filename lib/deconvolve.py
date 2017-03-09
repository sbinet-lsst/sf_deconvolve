# -*- coding: utf-8 -*-

"""PSF DECONVOLUTION MODULE

This module deconvolves a set of galaxy images with a known object-variant PSF.

:Author: Samuel Farrens <samuel.farrens@gmail.com>

:Version: 5.0

:Date: 12/12/2016

"""

from scipy.linalg import norm
from operators import *
from algorithms import PowerMethod
from cost import *
from linear import *
from proximity import *
from optimisation import *
from reweight import cwbReweight
from wavelet import filter_convolve, filter_convolve_stack
from transform import cube2matrix
from functions.stats import sigma_mad


def get_lambda(n_images, p_pixel, sigma, spec_rad):
    """Get lambda value

    This method calculates the singular value threshold for low-rank
    regularisation

    Parameters
    ----------
    n_images : int
        Total number of images
    p_pixel : int
        Total number of pixels
    sigma : float
        Noise standard deviation
    spec_rad : float
        The spectral radius of the gradient operator

    Returns
    -------
    float Lambda value

    """

    return sigma * np.sqrt(np.max([n_images + 1, p_pixel])) * spec_rad


def run(data, psf, **kwargs):
    """Run deconvolution

    This method initialises the operator classes and runs the optimisation
    algorithm

    Parameters
    ----------
    data : np.ndarray
        Input data array, an array of 2D images
    psf : np.ndarray
        Input PSF array, a single 2D PSF or an array of 2D PSFs
    **kwargs
        Arbitrary keyword arguments

    Returns
    -------
    np.ndarray decconvolved data

    Raises
    ------
    ValueError
        If the size of `wave_thresh_factor` does not match the size of
        `wavelet_levels`

    """

    ######
    # SET NOISE ESTIMATE

    if isinstance(kwargs['noise_est'], type(None)):
        kwargs['noise_est'] = sigma_mad(data)
    print ' - Noise Estimate:', kwargs['noise_est']
    kwargs['log'].info(' - Noise Estimate: ' + str(kwargs['noise_est']))

    ######
    # SET THE GRADIENT OPERATOR

    if kwargs['grad']:
        grad_op = StandardPSF(data, psf, psf_type=kwargs['psf_type'])

    else:
        grad_op = StandardPSFnoGrad(data, psf, psf_type=kwargs['psf_type'])

    grad_op.get_spec_rad(tolerance=1e-6, max_iter=10)
    print ' - Spectral Radius:', grad_op.spec_rad
    kwargs['log'].info(' - Spectral Radius: ' + str(grad_op.spec_rad))

    ######
    # SET THE LINEAR OPERATOR(S)

    if kwargs['mode'] == 'all':
        linear_op = LinearCombo([Wavelet(data, kwargs['wavelet_opt']),
                                Identity()])
        wavelet_filters = linear_op.operators[0].filters
        linear_l1norm = linear_op.operators[0].l1norm

    elif kwargs['mode'] in ('lowr', 'grad'):
        linear_op = Identity()
        linear_l1norm = linear_op.l1norm

    elif kwargs['mode'] == 'sparse':
        linear_op = Wavelet(data, kwargs['wavelet_opt'])
        wavelet_filters = linear_op.filters
        linear_l1norm = linear_op.l1norm

    ######
    # ESTIMATE THE NOISE IN THE WAVELET DOMAIN

    # if kwargs['wave_thresh_factor'].size == 1:
    #     kwargs['wave_thresh_factor'] = np.repeat(kwargs['wave_thresh_factor
    # '],
    #                                              kwargs['wavelet_levels'])
    #
    # elif kwargs['wave_thresh_factor'].size != kwargs['wavelet_levels']:
    #     raise ValueError('The number of wavelet threshold factors does not
    # ' +
    #                      'match the number of wavelet levels.')

    if kwargs['mode'] in ('all', 'sparse'):

        if kwargs['psf_type'] == 'fixed':

            filter_conv = (filter_convolve(np.rot90(psf, 2),
                           wavelet_filters))

            filter_norm = np.array([norm(a) * b * np.ones(data.shape[1:])
                                    for a, b in zip(filter_conv,
                                    kwargs['wave_thresh_factor'])])

            filter_norm = np.array([filter_norm for i in
                                    xrange(data.shape[0])])

        else:

            filter_conv = (filter_convolve_stack(np.rot90(psf, 2),
                           wavelet_filters))

            filter_norm = np.array([[norm(b) * c * np.ones(data.shape[1:])
                                    for b, c in zip(a,
                                    kwargs['wave_thresh_factor'])]
                                    for a in filter_conv])

        weight_est = kwargs['noise_est'] * filter_norm

    # SET THE WEIGHTS
        rw = cwbReweight(weight_est)

    # SET THE SHAPE OF THE DUAL VARIABLE
        dual_shape = ([wavelet_filters.shape[0]] +
                      list(data.shape))

        dual_shape[0], dual_shape[1] = dual_shape[1], dual_shape[0]

    ######
    # SET RHO, SIGMA AND TAU FOR CONDAT

    if kwargs['opt_type'] == 'condat':

        def get_sig_tau():
            return 1.0 / (grad_op.spec_rad + linear_l1norm)

        if isinstance(kwargs['condat_tau'], type(None)):
            condat_tau = get_sig_tau()
        else:
            condat_tau = kwargs['condat_tau']

        if isinstance(kwargs['condat_sigma'], type(None)):
            condat_sigma = get_sig_tau()
        else:
            condat_sigma = kwargs['condat_sigma']

        condat_rho = kwargs['relax']

        print ' - tau:', condat_tau
        print ' - sigma:', condat_sigma
        print ' - rho:', condat_rho
        kwargs['log'].info(' - tau: ' + str(condat_tau))
        kwargs['log'].info(' - sigma: ' + str(condat_sigma))
        kwargs['log'].info(' - rho: ' + str(condat_rho))

        sig_tau_test = (1.0 / condat_tau - condat_sigma * linear_l1norm ** 2 >=
                        grad_op.spec_rad / 2.0)

        print ' - 1/tau - sigma||L||^2 >= beta/2:', sig_tau_test
        kwargs['log'].info(' - 1/tau - sigma||L||^2 >= beta/2: ' +
                           str(sig_tau_test))

    ######
    # FIND LAMBDA FOR LOW-RANK.

    if kwargs['mode'] in ('all', 'lowr'):

        # lamb = kwargs['lowr_thresh_factor'] * kwargs['noise_est']
        lamb = (kwargs['lowr_thresh_factor'] * get_lambda(data.shape[0],
                np.prod(data.shape[1:]), kwargs['noise_est'],
                grad_op.spec_rad))

        print ' - lambda:', lamb
        kwargs['log'].info(' - lambda: ' + str(lamb))

    ######
    # INITALISE THE PRIMAL AND DUAL VALUES

    # 1 Primal Operator (Positivity or Identity)
    if isinstance(kwargs['primal'], type(None)):
        kwargs['primal'] = np.ones(data.shape)

    if kwargs['mode'] == 'all':

        # 2 Dual Operators (Wavelet + Threshold and Identity + LowRankMatrix)
        dual = np.empty(2, dtype=np.ndarray)
        dual[0] = np.ones(dual_shape)
        dual[1] = np.ones(data.shape)

    elif kwargs['mode'] in ('lowr', 'grad'):

        # 1 Dual Operator (Identity or LowRankMatrix)
        dual = np.ones(data.shape)

    elif kwargs['mode'] == 'sparse':

        # 1 Dual Operator (Wavelet Threshold)
        dual = np.ones(dual_shape)

    print ' - Primal Variable Shape:', kwargs['primal'].shape
    print ' - Dual Variable Shape:', dual.shape
    print ' ----------------------------------------'

    ######
    # GET THE INITIAL GRADIENT VALUE
    grad_op.get_grad(kwargs['primal'])

    ######
    # SET THE PROXIMITY OPERATORS

    # FOR GFWBW
    prox_list = []

    if kwargs['pos']:
        prox_op = Positive()
        prox_list.append(prox_op)

    else:
        prox_op = Identity()

    use_pos = False

    if kwargs['opt_type'] == 'fwbw':
        use_pos = True

    total_n_iter = kwargs['n_iter'] * (kwargs['n_reweights'] + 1)
    cost_test_window = kwargs['cost_window']

    if kwargs['mode'] == 'all':

        prox_dual_op = ProximityCombo([Threshold(rw.weights,),
                                      LowRankMatrix(lamb,
                                      thresh_type=kwargs['lowr_thresh_type'],
                                      lowr_type=kwargs['lowr_type'],
                                      operator=grad_op.MtX)])

        cost_op = costFunction(data, grad=grad_op,
                               wavelet=linear_op.operators[0],
                               weights=rw.weights, lambda_reg=lamb,
                               mode=kwargs['mode'], positivity=kwargs['pos'],
                               window=cost_test_window,
                               output=kwargs['output'])

    elif kwargs['mode'] == 'lowr':

        prox_dual_op = LowRankMatrix(lamb,
                                     thresh_type=kwargs['lowr_thresh_type'],
                                     lowr_type=kwargs['lowr_type'],
                                     operator=grad_op.MtX)

        prox_list.append(prox_dual_op)

        cost_op = costFunction(data, grad=grad_op, wavelet=None, weights=None,
                               lambda_reg=lamb, mode=kwargs['mode'],
                               positivity=kwargs['pos'],
                               window=cost_test_window,
                               output=kwargs['output'])

    elif kwargs['mode'] == 'sparse':

        prox_dual_op = Threshold(rw.weights)

        cost_op = costFunction(data, grad=grad_op, wavelet=linear_op,
                               weights=rw.weights, lambda_reg=None,
                               mode=kwargs['mode'], positivity=kwargs['pos'],
                               window=cost_test_window,
                               output=kwargs['output'])

    elif kwargs['mode'] == 'grad':

        prox_dual_op = Identity()
        prox_list.append(prox_dual_op)

        cost_op = costFunction(data, grad=grad_op, wavelet=None, weights=None,
                               lambda_reg=None, mode=kwargs['mode'],
                               positivity=kwargs['pos'],
                               window=cost_test_window,
                               output=kwargs['output'])

    ######
    # PERFORM THE OPTIMISATION

    if kwargs['opt_type'] == 'fwbw':
        opt = ForwardBackward(kwargs['primal'], grad_op, prox_dual_op,
                              cost_op, auto_iterate=False)

    elif kwargs['opt_type'] == 'condat':
        opt = Condat(kwargs['primal'], dual, grad_op, prox_op, prox_dual_op,
                     linear_op, cost_op, rho=condat_rho, sigma=condat_sigma,
                     tau=condat_tau,
                     auto_iterate=False)

    elif kwargs['opt_type'] == 'gfwbw':
        opt = GenForwardBackward(kwargs['primal'], grad_op, prox_list,
                                 lambda_init=1.0, cost=cost_op,
                                 weights=[0.1, 0.9],
                                 auto_iterate=False, plot=True)

    opt.iterate(max_iter=kwargs['n_iter'])

    print ''

    ######
    # REWEIGHTING

    if kwargs['mode'] in ('all', 'sparse'):

        for i in xrange(kwargs['n_reweights']):

            print ' Reweight:', i + 1
            print ''

            rw.reweight(linear_op.op(opt.x_new)[0])
            if kwargs['mode'] == 'all':
                prox_dual_op.operators[0].update_weights(rw.weights)
            else:
                prox_dual_op.update_weights(rw.weights)
            cost_op.update_weights(rw.weights)
            opt.iterate(max_iter=kwargs['n_iter'])
            print ''

    ######
    # FINISH AND RETURN RESULTS

    kwargs['log'].info(' - Final iteration number: ' + str(cost_op.iteration))
    kwargs['log'].info(' - Final log10 cost value: ' +
                       str(np.log10(cost_op.cost)))
    kwargs['log'].info(' - Converged: ' + str(opt.converge))

    if kwargs['opt_type'] == 'condat':
        return opt.x_final, opt.y_final

    else:
        return opt.x_final, None