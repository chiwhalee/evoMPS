# -*- coding: utf-8 -*-
"""
Created on Thu Oct 13 17:29:27 2011

@author: Ashley Milsted

"""
import numpy as np
import scipy as sp
import scipy.linalg as la
import scipy.sparse.linalg as las
import scipy.optimize as opti
import nullspace as ns
import matmul as m
import math as ma

try:
    import tdvp_common as tc
except ImportError:
    tc = None
    print "Warning! Cython version of Calc_C was not available. Performance may suffer for large q."
        
#This class allows us to use scipy's bicgstab implementation
class PPInvOp:
    tdvp = None
    l = None
    r = None
    A = None
    p = 0
    
    shape = (0)
    
    dtype = None
    
    left = False
    
    def __init__(self, tdvp, p=0, left=False):
        self.tdvp = tdvp
        self.l = tdvp.l
        self.r = tdvp.r
        self.p = 0
        self.left = left
        
        self.D = tdvp.D
        
        self.shape = (self.D**2, self.D**2)
        
        self.dtype = tdvp.typ
        
        self.out = np.empty_like(self.l)
    
    def matvec(self, v):
        x = v.reshape((self.D, self.D))
        
        if self.left:
            xE = self.tdvp._eps_l_noop_dense_A(x, self.out)
            QEQ = xE - m.H(self.l) * m.adot(self.r, x)
        else:
            Ex = self.tdvp._eps_r_noop_dense_A(x, self.out)
            QEQ = Ex - self.r * m.adot(self.l, x)        
        
        
        if not self.p == 0:
            QEQ *= np.exp(1.j * self.p)
        
        res = x - QEQ
        
        return res.ravel()

class HTangentOp:
    tdvp = None
    ppinvop = None
    p = 0
    
    def __init__(self, tdvp, p):
        self.tdvp = tdvp
        self.p = p
        self.ppinvop = PPInvOp(tdvp, p)
        
        self.shape = (tdvp.D**2, tdvp.D**2)
        self.dtype = tdvp.typ        
        
    def matvec(self, v):
        x = v.reshape((self.tdvp.D, self.tdvp.D * (self.tdvp.q)))
        
        self.tdvp.calc_BHB(x)                
        
class EvoMPS_TDVP_Uniform:
    odr = 'C'
    typ = np.complex128
        
    def __init__(self, D, q):
        
        self.itr_rtol = 1E-13
        self.itr_atol = 1E-14
        
        self.h_nn = None    
        self.h_nn_cptr = None
        
        self.symm_gauge = False
        
        self.sanity_checks = False
        self.check_fac = 50
        
        self.userdata = None        
        
        self.eps = np.finfo(self.typ).eps
        
        self.eta = 0
        
        self._init_arrays(D, q)
        
        #self.A.fill(0)
        #for s in xrange(q):
        #    self.A[s] = np.eye(D)
            
        self.randomize()

    def randomize(self, fac=0.5):
        m.randomize_cmplx(self.A, a=-fac, b=fac)
    
    def _init_arrays(self, D, q):
        self.D = D
        self.q = q
        
        self.A = np.empty((q, D, D), dtype=self.typ, order=self.odr)
        self.AA = np.empty((q, q, D, D), dtype=self.typ, order=self.odr)
        
        self.C = np.empty((q, q, D, D), dtype=self.typ, order=self.odr)
        
        self.K = np.ones_like(self.A[0])
        self.K_left = None
        
        self.l = np.ones_like(self.A[0])
        self.r = np.ones_like(self.A[0])
        self.conv_l = True
        self.conv_r = True
        
        self.tmp = np.empty_like(self.A[0])
           
    def _eps_r_noop_dense_A(self, x, out):
        """The right epsilon map, optimized for efficiency.
        """
        A = self.A
        out.fill(0)
        dot = np.dot
        for s in xrange(self.q):
            out += dot(A[s], dot(x, A[s].conj().T))
        
        return out
        
    def eps_r(self, x, A1=None, A2=None, op=None, out=None):
        """Implements the right epsilon map
        
        FIXME: Ref.
        
        Parameters
        ----------
        op : function
            The single-site operator to use.
        out : ndarray
            A matrix to hold the result (with the same dimensions as r).
        x : ndarray
            The argument matrix.
    
        Returns
        -------
        res : ndarray
            The resulting matrix.
        """
        if out is None:
            out = np.zeros_like(self.A[0])
        else:
            out.fill(0.)
            
        if A1 is None:
            A1 = self.A
        if A2 is None:
            A2 = self.A
            
        if op is None:
            for s in xrange(self.q):
                out += m.mmul(A1[s], x, m.H(A2[s]))
        else:
            for s in xrange(self.q):
                for t in xrange(self.q):
                    o_st = op(s, t)
                    if o_st != 0.:
                        tmp = m.mmul(A1[t], x, m.H(A2[s]))
                        tmp *= o_st
                        out += tmp
        return out
        
    def _eps_l_noop_dense_A(self, x, out):
        """The left epsilon map, optimized for efficiency.
        """
        A = self.A
        out.fill(0)
        dot = np.dot
        for s in xrange(self.q):
            out += dot(A[s].conj().T, dot(x, A[s]))
            
        return out
        
    def eps_l(self, x, out=None):
        if out is None:
            out = np.zeros_like(self.A[0])
        else:
            out.fill(0.)
            
        for s in xrange(self.q):
            out += m.mmul(m.H(self.A[s]), x, self.A[s])        
            
        return out
        
    def calc_AA(self):
        dot = np.dot
        A = self.A
        AA = self.AA
        for s in xrange(self.q):
            for t in xrange(self.q):
                AA[s, t] = dot(A[s], A[t])
                #Use dot() because A[s] is always a dense matrix
        
        #Note: This could be cythonized, calling BLAS from C    
        
        #This works too: (just for reference)
        #AA = np.array([dot(A[s], A[t]) for s in xrange(self.q) for t in xrange(self.q)])
        #self.AA = AA.reshape(self.q, self.q, self.D, self.D)
        
    def eps_r_2s(self, x, op, A1=None, A2=None, A3=None, A4=None):
        res = np.zeros_like(self.A[0])
        
        if A1 is None:
            A1 = self.A
        if A2 is None:
            A2 = self.A
        if A3 is None:
            A3 = self.A
        if A4 is None:
            A4 = self.A
            
        if (A1 is self.A) and (A2 is self.A) and (A3 is self.A) and (A4 is self.A):
            for u in xrange(self.q):
                for v in xrange(self.q):
                    subres = np.zeros_like(self.A[0])
                    for s in xrange(self.q):
                        for t in xrange(self.q):
                            opval = op(u, v, s, t)
                            if opval != 0:
                                subres += opval * self.AA[s, t]
                    res += m.mmul(subres, x, m.H(self.AA[u, v]))
        else:
            AAuvH = np.empty_like(self.A[0])
            for u in xrange(self.q):
                for v in xrange(self.q):
                    AAuvH = m.H(m.mmul(A3[u], A4[v]), out=AAuvH)
                    subres = np.zeros_like(self.A[0])
                    for s in xrange(self.q):
                        for t in xrange(self.q):
                            opval = op(u, v, s, t)
                            if opval != 0:
                                subres += opval * np.dot(A1[s], A2[t])
                    res += m.mmul(subres, x, AAuvH)
                    
        return res

    def _calc_lr_brute(self):
        E = np.zeros((self.D**2, self.D**2), dtype=self.typ, order='C')
        
        for s in xrange(self.q):
            E += sp.kron(self.A[s], self.A[s].conj())
            
        ev, eVL, eVR = la.eig(E, left=True, right=True)
        
        i = np.argmax(ev)
        
        self.A *= 1 / sp.sqrt(ev[i])        
        
        self.l = eVL[:,i].reshape((self.D, self.D))
        self.r = eVR[:,i].reshape((self.D, self.D))
        
        norm = m.adot(self.l, self.r)
        self.l *= 1 / sp.sqrt(norm)
        self.r *= 1 / sp.sqrt(norm)        
        
        print "Sledgehammer:"
        print "Left ok?: " + str(np.allclose(self.eps_l(self.l), self.l))
        print "Right ok?: " + str(np.allclose(self.eps_r(self.r), self.r))
        
    def _calc_lr(self, x, eps, tmp, max_itr=2000, rtol=1E-14, atol=1E-14):
        """Power iteration to obtain eigenvector corresponding to largest
           eigenvalue.
           
           The contents of the starting vector x is modifed.
           
           Why do we require more iterations for larger q and D?
        """
        norm = la.fblas.dznrm2 #NOTE: assuming complex128
        #allclose = np.allclose
        
        x *= 1/norm(x.ravel())
        for i in xrange(max_itr):
            eps(x, out=tmp)
            ev = norm(tmp.ravel())
            tmp *= (1 / ev)
            #if allclose(tmp, x, atol=atol, rtol=rtol): #allclose is SLOW!
            if norm((tmp - x).ravel()) < atol:
                x[:] = tmp
                break
            x[:] = tmp
        
        #re-scale
        if not abs(ev - 1) < atol:
            self.A *= 1 / ma.sqrt(ev)
            if self.sanity_checks:
                ev = norm(eps(x, out=tmp).ravel())
                if not abs(ev - 1) < atol:
                    print "Sanity check failed: Largest ev after re-scale = %g" % ev
        
        return x, i < max_itr - 1, i
    
    def calc_lr(self):        
        tmp = np.empty_like(self.tmp)

        self.l = np.asarray(self.l)

        self.r = np.asarray(self.r)
        
        self.l, self.conv_l, self.itr_l = self._calc_lr(self.l, 
                                                        self._eps_l_noop_dense_A, 
                                                        tmp, 
                                                        rtol=self.itr_rtol, 
                                                        atol=self.itr_atol)
        
        self.r, self.conv_r, self.itr_r = self._calc_lr(self.r, 
                                                        self._eps_r_noop_dense_A, 
                                                        tmp, 
                                                        rtol=self.itr_rtol, 
                                                        atol=self.itr_atol)
        #normalize eigenvectors:

        if self.symm_gauge:
            norm = m.adot(self.l, self.r).real
            itr = 0 
            while not np.allclose(norm, 1, atol=1E-13, rtol=0) and itr < 10:
                self.l *= 1. / ma.sqrt(norm)
                self.r *= 1. / ma.sqrt(norm)
                
                norm = m.adot(self.l, self.r).real
                
                itr += 1
                
            if itr == 10:
                print "Warning: Max. iterations reached during normalization!"
        else:
            fac = self.D / np.trace(self.r).real
            self.l *= 1 / fac
            self.r *= fac

            norm = m.adot(self.l, self.r).real
            itr = 0 
            while not np.allclose(norm, 1, atol=1E-13, rtol=0) and itr < 10:
                self.l *= 1. / norm
                norm = m.adot(self.l, self.r).real
                itr += 1
                
            if itr == 10:
                print "Warning: Max. iterations reached during normalization!"

        if self.sanity_checks:
            if not np.allclose(self.eps_l(self.l), self.l,
            rtol=self.itr_rtol*self.check_fac, 
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Left eigenvector bad! Off by: " \
                       + str(la.norm(self.eps_l(self.l) - self.l))
                       
            if not np.allclose(self.eps_r(self.r), self.r,
            rtol=self.itr_rtol*self.check_fac,
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Right eigenvector bad! Off by: " \
                       + str(la.norm(self.eps_r(self.r) - self.r))
            
            if not np.allclose(self.l, m.H(self.l),
            rtol=self.itr_rtol*self.check_fac, 
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: l is not hermitian!"

            if not np.allclose(self.r, m.H(self.r),
            rtol=self.itr_rtol*self.check_fac, 
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: r is not hermitian!"
            
            if not np.all(la.eigvalsh(self.l) > 0):
                print "Sanity check failed: l is not pos. def.!"
                
            if not np.all(la.eigvalsh(self.r) > 0):
                print "Sanity check failed: r is not pos. def.!"
            
            norm = m.adot(self.l, self.r)
            if not np.allclose(norm, 1.0, atol=1E-13, rtol=0):
                print "Sanity check failed: Bad norm = " + str(norm)
    
    def restore_SCF(self):
        X = la.cholesky(self.r, lower=True)
        Y = la.cholesky(self.l, lower=False)
        
        U, sv, Vh = la.svd(Y.dot(X))
        
        #s contains the Schmidt coefficients,
        lam = sv**2
        self.S_hc = - np.sum(lam * sp.log2(lam))
        
        S = m.simple_diag_matrix(sv, dtype=self.typ)
        Srt = S.sqrt()
        
        g = m.mmul(Srt, Vh, m.invtr(X, lower=True))
        
        g_i = m.mmul(m.invtr(Y, lower=False), U, Srt)
        
        for s in xrange(self.q):
            self.A[s] = m.mmul(g, self.A[s], g_i)
                
        if self.sanity_checks:
            Sfull = np.asarray(S)
            
            if not np.allclose(g.dot(g_i), np.eye(self.D)):
                print "Sanity check failed! Restore_SCF, bad GT!"
            
            l = m.mmul(m.H(g_i), self.l, g_i)
            r = m.mmul(g, self.r, m.H(g))
            
            if not np.allclose(Sfull, l):
                print "Sanity check failed: Restorce_SCF, left failed!"
                
            if not np.allclose(Sfull, r):
                print "Sanity check failed: Restorce_SCF, right failed!"
                
            l = self.eps_l(Sfull)
            r = self.eps_r(Sfull)
            
            if not np.allclose(Sfull, l, rtol=self.itr_rtol*self.check_fac, 
                               atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Restorce_SCF, left bad!"
                
            if not np.allclose(Sfull, r, rtol=self.itr_rtol*self.check_fac, 
                               atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Restorce_SCF, right bad!"

        self.l = S
        self.r = S
    
    def restore_CF(self, ret_g=False):
        if self.symm_gauge:
            self.restore_SCF()
        else:
            #First get G such that r = eye
            G = la.cholesky(self.r, lower=True)
            G_i = m.invtr(G, lower=True)

            self.l = m.mmul(m.H(G), self.l, G)
            
            #Now bring l into diagonal form, trace = 1 (guaranteed by r = eye..?)
            ev, EV = la.eigh(self.l)
            
            G = G.dot(EV)
            G_i = m.H(EV).dot(G_i)
            
            for s in xrange(self.q):
                self.A[s] = m.mmul(G_i, self.A[s], G)
                
            #ev contains the squares of the Schmidt coefficients,
            self.S_hc = - np.sum(ev * sp.log2(ev))
            
            self.l = m.simple_diag_matrix(ev, dtype=self.typ)

            if self.sanity_checks:
                M = np.zeros_like(self.r)
                for s in xrange(self.q):
                    M += m.mmul(self.A[s], m.H(self.A[s]))            
                
                self.r = m.mmul(G_i, self.r, m.H(G_i))
                
                if not np.allclose(M, self.r, 
                                   rtol=self.itr_rtol*self.check_fac,
                                   atol=self.itr_atol*self.check_fac):
                    print "Sanity check failed: RestoreRCF, bad M."
                    print "Off by: " + str(la.norm(M - self.r))
                    
                if not np.allclose(self.r, np.eye(self.D),
                                   rtol=self.itr_rtol*self.check_fac,
                                   atol=self.itr_atol*self.check_fac):
                    print "Sanity check failed: r not identity."
                    print "Off by: " + str(la.norm(np.eye(self.D) - self.r))
                
                l = self.eps_l(self.l)
                r = self.eps_r(self.r)
                
                if not np.allclose(r, self.r,
                                   rtol=self.itr_rtol*self.check_fac, 
                                   atol=self.itr_atol*self.check_fac):
                    print "Sanity check failed: Restore_RCF, bad r!"
                    print "Off by: " + str(la.norm(r - self.r))

                if not np.allclose(l, self.l,
                                   rtol=self.itr_rtol*self.check_fac, 
                                   atol=self.itr_atol*self.check_fac):
                    print "Sanity check failed: Restore_RCF, bad l!"
                    print "Off by: " + str(la.norm(l - self.l))
        
            self.r = m.eyemat(self.D, dtype=self.typ)
        
        if ret_g:
            return G, G_i
        else:
            return
    
    def calc_C(self):
        if not tc is None and not self.h_nn_cptr is None:
            self.C = tc.calc_C(self.AA, self.h_nn_cptr, self.C)
        else:
            self.C.fill(0)
            
            for u in xrange(self.q): #ndindex is just too slow..
                for v in xrange(self.q):
                    for s in xrange(self.q):
                        for t in xrange(self.q):
                            h = self.h_nn(s, t, u, v) #for large q, this executes a lot..
                            if h != 0:
                                self.C[s, t] += h * self.AA[u, v]
    
    def calc_PPinv(self, x, p=0, out=None, left=False):
        if out is None:
            out = np.ones_like(self.A[0])
        
        op = PPInvOp(self, p, left)
        
        if left:
            res = m.H(out).ravel()
            x = m.H(x).ravel()
        else:
            res = out.ravel()
            x = x.ravel()
        
        res, info = las.bicgstab(op, x, x0=res, maxiter=1000, 
                                 tol=self.itr_rtol)
        
        if info > 0:
            print "Warning: Did not converge on solution for ppinv!"
        
        #Test
        if self.sanity_checks:
            RHS_test = op.matvec(res)
            if not np.allclose(RHS_test, x, rtol=self.itr_rtol*self.check_fac,
                                atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Bad ppinv solution! Off by: " + str(
                        la.norm(RHS_test - x))
        
        res = res.reshape((self.D, self.D))
        
        if left:
            res = m.H(res)
        
        out[:] = res
        
        return out
        
    def calc_K(self):
        Hr = np.zeros_like(self.A[0])
        
        for s in xrange(self.q):
            for t in xrange(self.q):
                Hr += m.mmul(self.C[s, t], self.r, m.H(self.AA[s, t]))
        
        self.h = m.adot(self.l, Hr)
        
        QHr = Hr - self.r * self.h
        
        self.calc_PPinv(QHr, out=self.K)
        
        if self.sanity_checks:
            Ex = self.eps_r(self.K)
            QEQ = Ex - self.r * m.adot(self.l, self.K)
            res = self.K - QEQ
            if not np.allclose(res, QHr):
                print "Sanity check failed: Bad K!"
                print "Off by: " + str(la.norm(res - QHr))
        
    def calc_K_l(self):
        lH = np.zeros_like(self.A[0])
        
        for s in xrange(self.q):
            for t in xrange(self.q):
                lH += m.mmul(m.H(self.AA[s, t]), self.l, self.C[s, t])
        
        h = m.adot(lH, self.r)
        
        lHQ = lH - self.l * h
        
        self.K_left = self.calc_PPinv(lHQ, left=True, out=self.K_left)
        
        if self.sanity_checks:
            xE = self.eps_l(self.K_left)
            QEQ = xE - self.l * m.adot(self.K_left, self.r)
            res = self.K_left - QEQ
            if not np.allclose(res, lHQ):
                print "Sanity check failed: Bad K_left!"
                print "Off by: " + str(la.norm(res - lHQ))
        
        
        return self.K_left, h
            
    def calc_Vsh(self, r_sqrt):
        R = np.zeros((self.D, self.q, self.D), dtype=self.typ, order='C')
        
        for s in xrange(self.q):
            R[:,s,:] = m.mmul(r_sqrt, m.H(self.A[s]))
        
        R = R.reshape((self.q * self.D, self.D))
        
        Vconj = ns.nullspace_qr(m.H(R)).T
        #R can be pretty huge for large q and D. The decomp. can take a long time...

        if self.sanity_checks:
            if not np.allclose(np.dot(Vconj, m.H(Vconj)), np.eye(self.q*self.D - self.D)):
                print "Sanity check failed: V . H(V) not eye!"
            if not np.allclose(np.dot(Vconj.conj(), R), 0):
                print "Sanity check failed: V . R not zero!"
        Vconj = Vconj.reshape(((self.q - 1) * self.D, self.D, self.q))
        
        #prepare for using V[s] and already take the adjoint, since we use it more often
        Vsh = Vconj.T
        Vsh = np.asarray(Vsh, order='C')

        return Vsh
        
    def calc_x(self, l_sqrt, l_sqrt_i, r_sqrt, r_sqrt_i, Vsh, out=None):
        if out is None:
            out = np.zeros((self.D, (self.q - 1) * self.D), dtype=self.typ, 
                           order=self.odr)
        
        tmp = np.zeros_like(out)
        for s in xrange(self.q):
            tmp2 = m.mmul(self.A[s], self.K)
            for t in xrange(self.q):
                tmp2 += m.mmul(self.C[s, t], self.r, m.H(self.A[t]))
            tmp += m.mmul(tmp2, r_sqrt_i, Vsh[s])
        out += l_sqrt.dot(tmp)
        
        tmp.fill(0)
        for s in xrange(self.q):
            tmp2.fill(0)
            for t in xrange(self.q):
                tmp2 += m.mmul(m.H(self.A[t]), self.l, self.C[t, s])
            tmp += m.mmul(tmp2, r_sqrt, Vsh[s])
        out += l_sqrt_i.dot(tmp)
        
        return out
        
    def get_B_from_x(self, x, Vsh, l_sqrt_i, r_sqrt_i, out=None):
        if out is None:
            out = np.zeros_like(self.A)
            
        for s in xrange(self.q):
            out[s] = m.mmul(l_sqrt_i, x, m.H(Vsh[s]), r_sqrt_i)
            
        return out
        
    def calc_l_r_roots(self):
        try:
            self.l_sqrt = self.l.sqrt()
            self.l_sqrt_i = self.l_sqrt.inv()
        except AttributeError:
            self.l_sqrt, evd = m.sqrtmh(self.l, ret_evd=True)
            self.l_sqrt_i = m.invmh(self.l_sqrt, evd=evd)
            
        try:
            self.r_sqrt = self.r.sqrt()
            self.r_sqrt_i = self.r_sqrt.inv()
        except AttributeError:
            self.r_sqrt, evd = m.sqrtmh(self.r, ret_evd=True)
            self.r_sqrt_i = m.invmh(self.r_sqrt, evd=evd)
        
        if self.sanity_checks:
            if not np.allclose(self.l_sqrt.dot(self.l_sqrt), self.l):
                print "Sanity check failed: l_sqrt is bad!"
            if not np.allclose(self.l_sqrt.dot(self.l_sqrt_i), np.eye(self.D)):
                print "Sanity check failed: l_sqrt_i is bad!"
            if not np.allclose(self.r_sqrt.dot(self.r_sqrt), self.r):
                print "Sanity check failed: r_sqrt is bad!"
            if (not np.allclose(self.r_sqrt.dot(self.r_sqrt_i), np.eye(self.D))):
                print "Sanity check failed: r_sqrt_i is bad!"
        
    def calc_B(self, set_eta=True):
        self.calc_l_r_roots()
                
        self.Vsh = self.calc_Vsh(self.r_sqrt)
        
        self.x = self.calc_x(self.l_sqrt, self.l_sqrt_i, self.r_sqrt, 
                        self.r_sqrt_i, self.Vsh)
        
        if set_eta:
            self.eta = sp.sqrt(m.adot(self.x, self.x))
        
        B = self.get_B_from_x(self.x, self.Vsh, self.l_sqrt_i, self.r_sqrt_i)
        
        if self.sanity_checks:
            #Test gauge-fixing:
            tst = np.zeros_like(self.A[0])
            for s in xrange(self.q):
                tst += m.mmul(B[s], self.r, m.H(self.A[s]))
            if not np.allclose(tst, 0):
                print "Sanity check failed: Gauge-fixing violation!"

        return B
        
    def update(self):
        self.calc_lr()
        self.restore_CF()
        self.calc_AA()
        self.calc_C()
        self.calc_K()
        
    def take_step(self, dtau, B=None):
        if B is None:
            B = self.calc_B()
        
        self.A += -dtau * B
            
    def take_step_RK4(self, dtau, B_i=None):
        def update():
            self.calc_lr()
            #self.restore_CF() #this really messes things up...
            self.calc_AA()
            self.calc_C()
            self.calc_K()            

        A0 = self.A.copy()
            
        B_fin = np.empty_like(self.A)

        if not B_i is None:
            B = B_i
        else:
            B = self.calc_B() #k1
        B_fin = B
        self.A = A0 - dtau/2 * B
        
        update()
        
        B = self.calc_B(set_eta=False) #k2                
        self.A = A0 - dtau/2 * B
        B_fin += 2 * B         
            
        update()
            
        B = self.calc_B(set_eta=False) #k3                
        self.A = A0 - dtau * B
        B_fin += 2 * B

        update()
        
        B = self.calc_B(set_eta=False) #k4
        B_fin += B
            
        self.A = A0 - dtau /6 * B_fin
            
    def calc_BHB(self, x):
        assert False
        
        B = self.B_from_x(x, self.Vsh, self.l_sqrt_i, self.r_sqrt_i)
        
        rVsh = self.Vsh.copy()
        for s in xrange(self.q):
            rVsh[s] = self.r_sqrt_i.dot(rVsh[s])
        
        res = self.l_sqrt.dot(self.eps_r_2ss(self.r, op=self.h_nn, A1=B, A3=rVsh))
        
        return None
            
    def find_min_h(self, B, dtau_init, tol=5E-2):
        dtau = dtau_init
        d = 1.0
        #dh_dtau = 0
        
        tau_min = 0
        
        A0 = self.A.copy()
        
        h_min = self.h
        A_min = self.A.copy()
        
        l_min = np.array(self.l, copy=True)
        r_min = np.array(self.r, copy=True)
        
        itr = 0
        while itr == 0 or itr < 30 and (abs(dtau) / tau_min > tol or tau_min == 0):
            itr += 1
            for s in xrange(self.q):
                self.A[s] = A_min[s] -d * dtau * B[s]
            
            self.l[:] = l_min
            self.r[:] = r_min
            
            self.calc_lr()
            self.calc_AA()
            
            self.h = self.expect_2s(self.h_nn)
            
            #dh_dtau = d * (self.h - h_min) / dtau
            
            print (tau_min + dtau, self.h.real, tau_min)
            
            if self.h.real < h_min.real:
                #self.restore_CF()
                h_min = self.h
                A_min[:] = self.A
                l_min[:] = self.l
                r_min[:] = self.r
                
                dtau = min(dtau * 1.1, dtau_init * 10)
                
#                if tau + d * dtau > 0:
                tau_min += d * dtau
#                else:
#                    d = -1.0
#                    dtau = tau
            else:
                d *= -1.0
                dtau = dtau / 2.0
                
#                if tau + d * dtau < 0:
#                    dtau = tau #only happens if dtau is -ive
                
        self.A = A0
        
        return tau_min
        
    def find_min_h_brent(self, B, dtau_init, tol=5E-2, skipIfLower=False, 
                         taus=[], hs=[], trybracket=True):
        def f(tau, *args):
            if tau == 0:
                return self.h.real
                
            try:
                i = taus.index(tau)
                return hs[i]
            except ValueError:
                for s in xrange(self.q):
                    self.A[s] = A0[s] - tau * B[s]
                
                self.calc_lr()
                self.calc_AA()
                
                h = self.expect_2s(self.h_nn)
                
                print (tau, h.real)
                
                res = h.real
                
                taus.append(tau)
                hs.append(res)
                
                return res
        
        A0 = self.A.copy()
        
        if skipIfLower:
            if f(dtau_init) < self.h.real:
                return dtau_init
        
        fb_brack = (dtau_init * 0.9, dtau_init * 1.1)
        if trybracket:
            brack = (dtau_init * 0.1, dtau_init, dtau_init * 2.0)
        else:
            brack = fb_brack
                
        try:
            tau_opt = opti.brent(f, 
                               brack=brack, 
                               tol=tol,
                               maxiter=20)
        except ValueError:
            print "Bracketing attempt failed..."
            tau_opt = opti.brent(f, 
                               brack=fb_brack, 
                               tol=tol,
                               maxiter=20)
        
        self.A = A0
        
        return tau_opt
        
    def step_reduces_h(self, B, dtau):
        A0 = self.A.copy()
        
        for s in xrange(self.q):
            self.A[s] = A0[s] - dtau * B[s]
        
        self.calc_lr()
        
        h = self.expect_2s(self.h_nn)
        
        self.A = A0
        
        return h.real < self.h.real, h

    def calc_B_CG(self, B_CG_0, x_0, eta_0, dtau_init, reset=False,
                 skipIfLower=False, brent=True):
        B = self.calc_B()
        eta = self.eta
        x = self.x
        
        if reset:
            beta = 0.
            print "RESET CG"
            
            B_CG = B
        else:
            beta = (eta**2) / eta_0**2
            
            #xy = m.adot(x_0, x)
            #betaPR = (eta**2 - xy) / eta_0**2
        
            print "BetaFR = " + str(beta)
            #print "BetaPR = " + str(betaPR)
        
            beta = max(0, beta.real)
        
            B_CG = B + beta * B_CG_0
        
        taus = []
        hs = []
        
        if skipIfLower:
            stepRedH, h = self.step_reduces_h(B_CG, dtau_init)
            taus.append(dtau_init)
            hs.append(h)
        
        if skipIfLower and stepRedH:
            tau = self.find_min_h(B_CG, dtau_init)
        else:
            if brent:
                tau = self.find_min_h_brent(B_CG, dtau_init, taus=taus, hs=hs,
                                            trybracket=False)
            else:
                tau = self.find_min_h(B_CG, dtau_init)
        
        if tau < 0:
            print "RESET due to negative dtau!"
            B_CG = B
            tau = self.find_min_h_brent(B_CG, dtau_init)
        
        return B_CG, B, x, eta, tau
        
            
    def expect_1s(self, op):
        Or = self.eps_r(self.r, op=op)
        
        return m.adot(self.l, Or)
            
    def expect_2s(self, op):
        res = self.eps_r_2s(self.r, op)
        
        return m.adot(self.l, res)
        
    def density_1s(self):
        rho = np.empty((self.q, self.q), dtype=self.typ)
        for s in xrange(self.q):
            for t in xrange(self.q):                
                rho[s, t] = m.adot(self.l, m.mmul(self.A[t], self.r, m.H(self.A[s])))
        return rho
        
    def apply_op_1s(self, o):
        newA = sp.zeros_like(self.A)
        
        for s in xrange(self.q):
            for t in xrange(self.q):
                newA[s] += self.A[t] * o(s, t)
                
        self.A = newA
            
    def save_state(self, file, userdata=None):
        if userdata is None:
            userdata = self.userdata

        l = np.asarray(self.l)
        r = np.asarray(self.r)
            
        tosave = np.empty((5), dtype=np.ndarray)
        tosave[0] = self.A
        tosave[1] = l
        tosave[2] = r
        tosave[3] = self.K
        tosave[4] = np.asarray(userdata)
        
        np.save(file, tosave)
        
    def load_state(self, file, expand=False, expand_q=False):
        state = np.load(file)
        
        newA = state[0]
        newl = state[1]
        newr = state[2]
        newK = state[3]
        if state.shape[0] > 4:
            self.userdata = state[4]
        
        if (newA.shape == self.A.shape):
            self.A[:] = newA
            self.K[:] = newK

            self.l = np.asarray(newl)
            self.r = np.asarray(newr)
                
            return True
        elif expand and (len(newA.shape) == 3) and (newA.shape[0] == 
        self.A.shape[0]) and (newA.shape[1] == newA.shape[2]) and (newA.shape[1]
        <= self.A.shape[1]):
            newD = self.D
            savedD = newA.shape[1]
            self._init_arrays(savedD, self.q)
            self.A[:] = newA
            self.l = newl
            self.r = newr
            self.K[:] = newK
            self.expand_D(newD)
            print "EXPANDED!"
        elif expand_q and (len(newA.shape) == 3) and (newA.shape[0] <= 
        self.A.shape[0]) and (newA.shape[1] == newA.shape[2]) and (newA.shape[1]
        == self.A.shape[1]):
            newQ = self.q
            savedQ = newA.shape[0]
            self._init_arrays(self.D, savedQ)
            self.A[:] = newA
            self.l = newl
            self.r = newr
            self.K[:] = newK
            self.expand_q(newQ)
            print "EXPANDED in q!"
        else:
            return False
            
    def expand_q(self, newq):
        if newq < self.q:
            return False
        
        oldq = self.q
        oldA = self.A
        oldK = self.K
        
        oldl = self.l
        oldr = self.r
        
        self._init_arrays(self.D, newq) 
        
        self.l = oldl
        self.r = oldr
        self.K = oldK
        
        self.A.fill(0)
        self.A[:oldq, :, :] = oldA
            
    def expand_D(self, newD):
        """Expands the bond dimension in a simple way.
        
        New matrix entries are (mostly) randomized.
        """
        if newD < self.D:
            return False
        
        oldD = self.D
        oldA = self.A
        oldK = self.K
        
        oldl = np.asarray(self.l)
        oldr = np.asarray(self.r)
        
        self._init_arrays(newD, self.q)
        
        realnorm = la.norm(oldA.real)
        imagnorm = la.norm(oldA.imag)
        realfac = (realnorm / (self.q * oldD**2))
        imagfac = (imagnorm / (self.q * oldD**2))
#        m.randomize_cmplx(newA[:, self.D:, self.D:], a=-fac, b=fac)
        m.randomize_cmplx(self.A[:, :oldD, oldD:], a=0, b=realfac, aj=0, bj=imagfac)
        m.randomize_cmplx(self.A[:, oldD:, :oldD], a=0, b=realfac, aj=0, bj=imagfac)
        self.A[:, oldD:, oldD:] = 0 #for nearest-neighbour hamiltonian

#        self.A[:, :oldD, oldD:] = oldA[:, :, :(newD - oldD)]
#        self.A[:, oldD:, :oldD] = oldA[:, :(newD - oldD), :]
        self.A[:, :oldD, :oldD] = oldA

        self.l[:oldD, :oldD] = oldl
        self.l[:oldD, oldD:].fill(la.norm(oldl) / oldD**2)
        self.l[oldD:, :oldD].fill(la.norm(oldl) / oldD**2)
        self.l[oldD:, oldD:].fill(la.norm(oldl) / oldD**2)
        
        self.r[:oldD, :oldD] = oldr
        self.r[oldD:, :oldD].fill(la.norm(oldr) / oldD**2)
        self.r[:oldD, oldD:].fill(la.norm(oldr) / oldD**2)
        self.r[oldD:, oldD:].fill(la.norm(oldr) / oldD**2)
        
        self.K[:oldD, :oldD] = oldK
        self.K[oldD:, :oldD].fill(la.norm(oldK) / oldD**2)
        self.K[:oldD, oldD:].fill(la.norm(oldK) / oldD**2)
        self.K[oldD:, oldD:].fill(la.norm(oldK) / oldD**2)
        
    def fuzz_state(self, f=1.0):
        norm = la.norm(self.A)
        fac = f*(norm / (self.q * self.D**2))        
        
        R = np.empty_like(self.A)
        m.randomize_cmplx(R, -fac/2.0, fac/2.0)
        
        self.A += R