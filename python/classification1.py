import numpy as np
import pylab as pb
import GPy
from truncnorm import truncnorm
from GPy.models.gradient_checker import GradientChecker
from scipy.special import erf
import tilted

class classification(GPy.core.Model):
    def __init__(self, X, Y, kern):
        self.X = X
        self.Y = Y
        self.kern = kern
        self.Y_sign = np.where(Y>0,1,-1)
        self.num_data, self.input_dim = self.X.shape
        GPy.core.Model.__init__(self)

        self.Ytilde = np.zeros(self.num_data)
        self.beta = np.zeros(self.num_data) + 1

        self.tilted = tilted.Heaviside(self.Y)

        self.ensure_default_constraints()
        self.constrain_positive('beta')

    def _set_params(self,x):
        self.Ytilde = x[:self.num_data]
        self.beta = x[self.num_data:2*self.num_data]
        self.kern._set_params_transformed(x[2*self.num_data:])

        #compute approximate posterior mean and variance - this is q(f) in RassWill notation,
        # and p(f | \tilde y) in ours
        self.K = self.kern.K(self.X)
        self.Ki, self.L, _,self.K_logdet = GPy.util.linalg.pdinv(self.K)
        self.Sigma,_,_,_ = GPy.util.linalg.pdinv(self.Ki + np.diag(self.beta))
        self.diag_Sigma = np.diag(self.Sigma)

        #TODO: use woodbury for inverse? We don't get Ki though :(
        #tmp = self.K + np.diag(1./self.beta)
        #L = GPy.util.linalg.jitchol(tmp)
        #LiK,_ = GPy.util.linalg.dtrtrs(L,self.K, lower=1)
        #self.Sigma_ = self.K - np.dot(LiK.T, LiK)
        #LiLiK,_ = GPy.util.linalg.dtrtrs(L, LiK, lower=1, trans=1)
        #self.SigmaKi_ = np.eye(self.num_data) - LiLiK.T

        self.mu = np.dot(self.Sigma, self.beta*self.Ytilde )

        #compute cavity means, vars (all at once!)
        self.cavity_vars = 1./(1./self.diag_Sigma - self.beta)
        self.cavity_means = self.cavity_vars * (self.mu/self.diag_Sigma - self.Ytilde*self.beta)

        #compute tilted distributions...
        self.tilted.set_cavity(self.cavity_means, self.cavity_vars)

    def _get_params(self):
        return np.hstack((self.Ytilde, self.beta, self.kern._get_params_transformed()))

    def _get_param_names(self):
        return ['Ytilde%i'%i for i in range(self.num_data)] +\
               ['beta%i'%i for i in range(self.num_data)] +\
               self.kern._get_param_names_transformed()

    def log_likelihood(self):
        #expectation of log pseudo-likelihood times prior under q
        A = -self.num_data*np.log(2*np.pi) + 0.5*np.log(self.beta).sum() - 0.5*self.K_logdet
        A += -0.5*np.sum(self.beta*(np.square(self.Ytilde) + np.square(self.tilted.mean)  + self.tilted.var - 2.*self.tilted.mean*self.Ytilde))
        tmp, _ = GPy.util.linalg.dtrtrs(self.L,self.tilted.mean, lower=1)
        A += -0.5*np.sum(np.square(tmp)) - 0.5*np.sum(np.diag(self.Ki)*self.tilted.var)

        #entropy
        B = self.tilted.H.sum()

        #relative likelihood/ pseudo-likelihood normalisers
        C = np.log(self.tilted.Z).sum()
        D = (.5 * self.num_data * np.log(2 * np.pi)
              + np.sum(.5 * np.log(1. / self.beta + self.cavity_vars)
                       + .5 * (self.Ytilde - self.cavity_means) ** 2 / (1. / self.beta + self.cavity_vars)))
        return A + B + C + D

    def _log_likelihood_gradients(self):
        """first compute gradients wrt cavity means/vars, then chain"""

        # partial derivatives: watch the broadcast!
        dcav_vars_dbeta = -(self.Sigma**2 / self.diag_Sigma**2 - np.eye(self.num_data) )*self.cavity_vars**2 # correct!
        dcav_means_dYtilde = (self.Sigma * self.beta[:, None] / self.diag_Sigma - np.diag(self.beta)) * self.cavity_vars # correct!

        dcav_means_dbeta = dcav_vars_dbeta * (self.mu / self.diag_Sigma - self.Ytilde * self.beta)
        tmp = self.Sigma / self.diag_Sigma
        dcav_means_dbeta += (tmp*(self.Ytilde[:,None] - self.mu[:,None]) + tmp**2*self.mu - np.diag(self.Ytilde))*self.cavity_vars

        #A
        dA_dYtilde =  self.beta * (self.tilted.mean - self.Ytilde)
        dA_dbeta = 0.5/self.beta - 0.5*(np.square(self.Ytilde) + np.square(self.tilted.mean) + self.tilted.var -2.*self.tilted.mean*self.Ytilde)
        dA_dq_means = self.beta*(self.Ytilde - self.tilted.mean) - np.dot(self.Ki, self.tilted.mean)
        dA_dq_vars = -0.5*(self.beta + np.diag(self.Ki))
        dA_dcav_vars = dA_dq_vars*self.tilted.dvar_dsigma2
        dA_dcav_vars += dA_dq_means*self.tilted.dmean_dsigma2
        dA_dcav_means = dA_dq_vars*self.tilted.dvar_dmu
        dA_dcav_means += dA_dq_means*self.tilted.dmean_dmu
        dA_dbeta += np.dot(dcav_means_dbeta, dA_dcav_means) + np.dot(dcav_vars_dbeta, dA_dcav_vars)
        dA_dYtilde += np.dot(dcav_means_dYtilde, dA_dcav_means)

        #B
        dB_dbeta = np.dot(dcav_means_dbeta, self.tilted.dH_dmu) + np.dot(dcav_vars_dbeta, self.tilted.dH_dsigma2)
        dB_dYtilde = np.dot(dcav_means_dYtilde, self.tilted.dH_dmu)

        #C
        dC_dbeta = np.dot(dcav_means_dbeta, self.tilted.dZ_dmu/self.tilted.Z) + np.dot(dcav_vars_dbeta, self.tilted.dZ_dsigma2/self.tilted.Z)
        dC_dYtilde = np.dot(dcav_means_dYtilde, self.tilted.dZ_dmu/self.tilted.Z)

        # D
        delta = np.eye(self.num_data)
        bv = 1. / self.beta + self.cavity_vars
        ym = self.Ytilde - self.cavity_means
        dD_dYtilde = np.dot(delta-dcav_means_dYtilde, ym/bv)
        dD_dcav_means = -ym / bv
        #TODO: tidy this monster!

        dD_dbeta = (.5 * np.sum((dcav_vars_dbeta - delta / self.beta ** 2) / bv, 1)
                    - np.sum(.5 * ym ** 2 * ((dcav_vars_dbeta - (delta / self.beta ** 2)) / bv ** 2)
                             + ym * dcav_means_dbeta / (1. / self.beta + self.cavity_vars), 1))
        dD_dcav_vars = 0.5 * (1-ym**2/bv)/bv

        #sum gradients from all the different parts
        dL_dbeta = dA_dbeta + dB_dbeta + dC_dbeta + dD_dbeta
        dL_dYtilde = dA_dYtilde + dB_dYtilde + dC_dYtilde + dD_dYtilde

        #ok, now gradient for K

        #TODO: tidy this monster!
        tmp0 = (dA_dcav_vars + self.tilted.dH_dsigma2 + self.tilted.dZ_dsigma2 / self.tilted.Z + dD_dcav_vars)
        SigmaB = 1 - self.diag_Sigma * self.beta
        mu_Sigma = self.mu / self.diag_Sigma
        B = (dA_dcav_means + self.tilted.dH_dmu + self.tilted.dZ_dmu / self.tilted.Z + dD_dcav_means)
        tmp0 += B*(mu_Sigma - self.Ytilde*self.beta)
        tmp0 /= SigmaB
        tmp0 -= B*mu_Sigma
        tmp0 /= SigmaB
        tmp1 = (dA_dcav_means + self.tilted.dH_dmu + self.tilted.dZ_dmu / self.tilted.Z + dD_dcav_means) / (1 - self.diag_Sigma * self.beta)
        
#         self.SigmaKi = self.Sigma.dot(self.Ki)
#         dL_dK = np.dot(self.SigmaKi.T * tmp0, self.SigmaKi)
#         dL_dK += np.dot(self.SigmaKi.T, tmp1)[:, None] * np.dot(self.Ki, self.mu)[None, :]
#         dL_dK -= 0.5*self.Ki
#         tmp2 = self.Ki.dot(self.tilted.mean)
#         dL_dK += 0.5 * (tmp2[:, None] * tmp2[None, :] + np.dot(self.Ki * self.tilted.var, self.Ki))

        dL_dK_inner = (tmp0 * self.Sigma).T + tmp1[:, None] * self.mu[None, :]
        dL_dK_inner = self.Sigma.dot(dL_dK_inner)
        dL_dK_inner += .5 * ((self.tilted.mean)[:, None] * self.tilted.mean[None, :] + delta * self.tilted.var)

        dL_dK = np.dot(self.Ki, np.dot(dL_dK_inner, self.Ki))
        dL_dK -= .5 * self.Ki
#         assert np.allclose(dL_dK, dL_dK0)
#         import ipdb;ipdb.set_trace()

        return np.hstack((dL_dYtilde, dL_dbeta, self.kern.dK_dtheta(dL_dK, self.X)))

    def _predict_raw(self, Xnew):
        """Predict the underlying GP function"""
        Kx = self.kern.K(Xnew, self.X)
        Kxx = self.kern.Kdiag(Xnew)
        L = GPy.util.linalg.jitchol(self.K + np.diag(1./self.beta))
        tmp, _ = GPy.util.linalg.dpotrs(L, self.Ytilde, lower=1)
        mu = np.dot(Kx, tmp)
        mu_ = np.dot(Kx, self.Ki).dot(self.mu)
        tmp, _ = GPy.util.linalg.dtrtrs(L, Kx.T, lower=1)
        var = Kxx - np.sum(np.square(tmp), 0)
        return mu, var


    def plot_f(self):
        if self.X.shape[1]==1:
            pb.figure()
            pb.errorbar(self.X[:,0],self.Ytilde,yerr=2*np.sqrt(1./self.beta), fmt=None, label='approx. likelihood', ecolor='r')
            Xtest, xmin, xmax = GPy.util.plot.x_frame1D(self.X)
            mu, var = self._predict_raw(Xtest)
            GPy.util.plot.gpplot(Xtest, mu, mu - 2*np.sqrt(var), mu + 2*np.sqrt(var))
        elif self.X.shape[1]==2:
            pb.figure()
            Xtest,xx,yy, xymin, xymax = GPy.util.plot.x_frame2D(self.X)
            mu, var = self._predict_raw(Xtest)
            pb.contour(xx,yy,mu.reshape(*xx.shape))

    def plot(self):
        if self.X.shape[1]==1:
            pb.figure()
            Xtest, xmin, xmax = GPy.util.plot.x_frame1D(self.X)
            mu, var = self._predict_raw(Xtest)

            #GPy.util.plot.gpplot(Xtest, mu, mu - 2*np.sqrt(var), mu + 2*np.sqrt(var))
            pb.plot(self.X, self.Y, 'kx', mew=1)
            pb.plot(Xtest, 0.5*(1+erf(mu/np.sqrt(2.*var))), linewidth=2)
            pb.ylim(-.1, 1.1)
        elif self.X.shape[1]==2:
            pb.figure()
            Xtest,xx,yy, xymin, xymax = GPy.util.plot.x_frame2D(self.X)
            mu, var = self._predict_raw(Xtest)
            p =0.5*(1+erf(mu/np.sqrt(2.*var)))
            c = pb.contour(xx,yy,p.reshape(*xx.shape), [0.1, 0.25, 0.5, 0.75, 0.9], colors='k')
            pb.clabel(c)
            i1 = self.Y==1
            pb.plot(self.X[:,0][i1], self.X[:,1][i1], 'rx', mew=2, ms=8)
            i2 = self.Y==0
            pb.plot(self.X[:,0][i2], self.X[:,1][i2], 'wo', mew=2, mec='b')




if __name__=='__main__':
    pb.close('all')
    N = 20
    X = np.random.rand(N)[:,None]
    X = np.sort(X,0)
    Y = np.zeros(N)
    Y[X[:, 0] < 3. / 4] = 1.
    Y[X[:, 0] < 1. / 4] = 0.
    #Y = np.random.permutation(Y)
    #pb.plot(X[:, 0], Y, 'kx')
    k = GPy.kern.rbf(1) + GPy.kern.white(1, 1e-5)
    m = classification(X, Y, k.copy())
    m.constrain_positive('beta')
    #m.randomize();     m.checkgrad(verbose=True)
    m.optimize('bfgs', messages=1)#, max_iters=20, max_f_eval=20)
    m.plot()

    mean, var = m._predict_raw(X)[:2]

    #pb.figure(2)
    #pb.clf()
    #pb.scatter(X[:, 0], Y, color='k', marker='x', s=40)
    #pb.scatter(X[:, 0], mean > 0, c='r', marker='o', facecolor='', edgecolor='r', s=50)


#
#     mm = GPy.models.GPClassification(X, Y[:, None], kernel=k.copy())
#     mm.constrain_fixed('')
#     mm.pseudo_EM()
#     mm.plot_f()
#     pb.errorbar(mm.X[:,0],mm.likelihood.Y[:,0],yerr=2*np.sqrt(1./mm.likelihood.precision[:,0]), fmt=None, color='r')
