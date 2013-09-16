import numpy as np
import pylab as pb
from scipy.special import erf

class Tilted:
    def __init__(self, Y):
        self.Y = Y
    def set_cavity(self, mu, sigma2):
        self.mu, self.sigma2 = mu, sigma2
        self.sigma = np.sqrt(self.sigma2)

def norm_cdf(x):
    return 0.5*(1+erf(x/np.sqrt(2.)))
def norm_pdf(x):
    return np.exp(-0.5*np.square(x))/np.sqrt(2.*np.pi)


class Heaviside(Tilted):
    def __init__(self, Y):
        Tilted.__init__(self,Y)
        self.Ysign = np.where(self.Y==1,1,-1)

    def set_cavity(self, mu, sigma2):
        Tilted.set_cavity(self, mu, sigma2)
        self.a = self.Ysign*self.mu/self.sigma
        self.Z = norm_cdf(self.a)
        self.N = norm_pdf(self.a)
        self.N_Z = self.N/self.Z
        self.N_Z2 = np.square(self.N_Z)
        self.N_Z3 = self.N_Z2*self.N_Z

        #compute moments
        self.mean = self.mu + self.Ysign*self.sigma*self.N_Z
        self.var = self.sigma2*(1. - self.a * self.N_Z - self.N_Z2)

        #derivatives of moments
        self.dmean_dmu = 1. - (self.N_Z2 + self.a * self.N_Z)
        self.dmean_dsigma2 = self.Ysign * self.N_Z * 0.5/self.sigma * (1 + self.a * (self.a + self.N_Z))
        #self.dvar_dmu = -self.Ysign*self.sigma*self.N_Z + self.Ysign*self.a*self.mu*self.N_Z - 3*self.mu*self.N_Z2 - 2*self.sigma*self.N_Z3
        self.dvar_dmu = -self.Ysign*self.sigma *self.N_Z + self.a * self.mu * self.N_Z + 3 * self.mu * self.N_Z2 + 2 * self.Ysign * self.sigma * self.N_Z3
        self.dvar_dsigma2 = 1 - self.N_Z * (self.N_Z + self.a * (.5 + .5*self.a**2 + self.N_Z * (1.5*self.a + self.N_Z)))

        #compute entropy
        self.H = 0.5*np.log(2*np.pi*self.sigma2) + np.log(self.Z) + 0.5*(self.mu**2 + self.var + self.mean**2 - 2*self.mean*self.mu)/self.sigma2

        #entropy derivatives
        self.dH_dmu = self.Ysign*(0.5 / self.sigma2) * (self.N_Z * (self.sigma + self.mu ** 2 / self.sigma) + self.Ysign*self.mu * self.N_Z2)
        self.dH_dsigma2 =(
                1./(2*self.sigma2) - self.N_Z * (self.a/(2*self.sigma2))
                + .5 * (1./(self.sigma2)) * (self.dvar_dsigma2 + (2*self.mean - 2*self.mu) * self.dmean_dsigma2)
                - 0.5/(self.sigma2**2) * (self.mu**2 + self.var + self.mean**2 - 2*self.mean*self.mu)
                )




if __name__=='__main__':
    from truncnorm import truncnorm
    mu = np.random.randn(2)
    sigma2 = np.exp(np.random.randn(2))
    Y = np.array([1,0])
    tns = [truncnorm(mu[0], sigma2[0], 'left'), truncnorm(mu[1], sigma2[1], 'right')]
    tilted = Heaviside(Y)
    tilted.set_cavity(mu, sigma2)


