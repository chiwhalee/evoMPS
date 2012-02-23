# -*- coding: utf-8 -*-
"""
Created on Thu Oct 13 17:29:27 2011

@author: Ashley Milsted

TODO:
    - Think about whether it is better to move back to RCF before
      applying B, since B does *right* gauge fixing. Then we would
      switch back to symm. form before calculating the next B.
       0. Restore RCF if needed
       1. RCF to SCF (with 4th root of l etc.)
       2. Calc B
       3. SCF to RCF (can do this quickly?)
       4. Apply B (take step)
    - Also, find out what happens in theory when this is *not* done...
      Should cause the gauge choice to drift... right?
    
    - Remove silly threading attempt?

"""
import scipy as sp
import scipy.linalg as la
import scipy.sparse.linalg as las
import nullspace as ns
import matmul as m
import tdvp_common as tc

def myMVop(opData, x):
    l = opData[0]
    r = opData[1]
    A = opData[2]
    
    opres = sp.zeros_like(A[0])
    for s in xrange(A.shape[0]):
        opres += m.matmul(None, A[s], x, m.H(A[s]))
        
    return x - opres  + r * sp.trace(sp.dot(l, x))

def myVVop(a, b, out=None):
    #return sp.trace(sp.dot(a, b))
    if out is None:
        #return sp.sum(sp.multiply(sp.conj(a), b))
        return sp.inner(a.ravel().conj(), b.ravel())
    else:
        #return sp.sum(sp.multiply(sp.conj(a, out=out), b, out=out))
        return sp.inner(sp.conj(a, out=out).ravel(), b.ravel())
        
#This class allows us to use scipy's bicgstab implementation
class KsuperOP:
    l = None
    r = None
    A = None
    
    shape = (0)
    
    dtype = None
    
    def __init__(self, l, r, A):
        self.l = l
        self.r = r
        self.A = A
        
        self.D = self.l.shape[0]
        
        self.shape = (self.D**2, self.D**2)
        
        self.dtype = A.dtype
    
    def matvec(self, v):
        x = v.reshape((self.D, self.D))
        
        opres = sp.zeros_like(self.A[0])
        for s in xrange(self.A.shape[0]):
            opres += m.matmul(None, self.A[s], x, m.H(self.A[s]))
            
        opres = x - opres  + self.r * sp.sum(sp.multiply(self.l.conj(), x))
            
        return opres.reshape((self.D**2))
        
        
class evoMPS_TDVP_Uniform:
    odr = 'C'
    typ = sp.complex128
    eps = 0
    
    itr_rtol = 1E-13
    itr_atol = 1E-14
    
    h_nn = None    
    h_nn_cptr = None
    
    symm_gauge = True
    
    sanity_checks = False
    check_fac = 50
    
    def __init__(self, D, q):
        self.eps = sp.finfo(self.typ).eps
        
        self.eta = 0
        
        self._init_arrays(D, q)
        
        #self.A.fill(0)
        #for s in xrange(q):
        #    self.A[s] = sp.eye(D)
            
        m.randomize_cmplx(self.A)         
    
    def _init_arrays(self, D, q):
        self.D = D
        self.q = q
        
        self.A = sp.empty((q, D, D), dtype=self.typ, order=self.odr)
        
        self.C = sp.empty((q, q, D, D), dtype=self.typ, order=self.odr)
        
        self.K = sp.ones_like(self.A[0])
        
        self.l = sp.ones_like(self.A[0])
        self.r = sp.ones_like(self.A[0])
        self.conv_l = True
        self.conv_r = True
        
        self.tmp = sp.empty_like(self.A[0])
           
    def EpsR(self, x, op=None, out=None):
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
            out = sp.zeros_like(self.r)
        else:
            out.fill(0.)
            
        if op is None:
            for s in xrange(self.q):
                out += m.matmul(self.tmp, self.A[s], x, m.H(self.A[s]))
        else:
            for (s, t) in sp.ndindex(self.q, self.q):
                o_st = op(s, t)
                if o_st != 0.:
                    m.matmul(self.tmp, self.A[t], x, m.H(self.A[s]))
                    self.tmp *= o_st
                    out += self.tmp
        return out
        
    def EpsL(self, x, out=None):
        if out is None:
            out = sp.zeros_like(self.l)
        else:
            out.fill(0.)
            
        for s in xrange(self.q):
            out += m.matmul(self.tmp, m.H(self.A[s]), x, self.A[s])        
            
        return out

    def _Calc_lr_brute(self):
        E = sp.zeros((self.D**2, self.D**2), dtype=self.typ, order='C')
        
        for s in xrange(self.q):
            E += sp.kron(self.A[s], self.A[s].conj())
            
        ev, eVL, eVR = la.eig(E, left=True, right=True)
        
        i = sp.argmax(ev)
        
        self.A *= 1 / sp.sqrt(ev[i])        
        
        self.l = eVL[:,i].reshape((self.D, self.D))
        self.r = eVR[:,i].reshape((self.D, self.D))
        
        norm = myVVop(self.l, self.r, out=self.tmp)
        self.l *= 1 / sp.sqrt(norm)
        self.r *= 1 / sp.sqrt(norm)        
        
        print "Sledgehammer:"
        print "Left ok?: " + str(sp.allclose(self.EpsL(self.l), self.l))
        print "Right ok?: " + str(sp.allclose(self.EpsR(self.r), self.r))
        
    def _Calc_lr(self, x, e, tmp, max_itr=1000, rtol=1E-14, atol=1E-14):        
        for i in xrange(max_itr):
            e(x, out=tmp)
            ev = la.norm(tmp)
            tmp *= (1 / ev)
            if sp.allclose(tmp, x, rtol=rtol, atol=atol):
                x[:] = tmp
                break
            x[:] = tmp
        
        #re-scale
        if not sp.allclose(ev, 1.0, rtol=rtol, atol=atol):
            self.A *= 1 / sp.sqrt(ev)
            ev = la.norm(e(x, out=tmp))
        
        return i < max_itr - 1, i
    
    def Calc_lr(self, renorm=True, force_r_CF=False):        
        tmp = sp.empty_like(self.tmp)
        
        self.conv_l, self.itr_l = self._Calc_lr(self.l, self.EpsL, tmp, 
                                                rtol=self.itr_rtol, 
                                                atol=self.itr_atol)
        
        self.conv_r, self.itr_r = self._Calc_lr(self.r, self.EpsR, tmp, 
                                                rtol=self.itr_rtol, 
                                                atol=self.itr_atol)
                    
        #normalize eigenvectors:
        #norm = sp.sum(sp.multiply(self.l.conj(), self.r, out=tmp))
        norm = myVVop(self.l, self.r, out=tmp)
        itr = 0
        while not sp.allclose(norm, 1, atol=1E-13, rtol=0) and itr < 20:
            self.l *= 1 / sp.sqrt(norm)
            self.r *= 1 / sp.sqrt(norm)
            
            norm = myVVop(self.l, self.r, out=tmp)
            
            itr += 1
            
        if itr == 20:
            print "Warning: Max. iterations reached during normalization!"
        
        if force_r_CF or not self.symm_gauge: #right to do this every time?
            fac = self.D / sp.trace(self.r)
            self.l *= 1 / fac
            self.r *= fac

        if self.sanity_checks:
            if not sp.allclose(self.EpsL(self.l), self.l,
            rtol=self.itr_rtol*self.check_fac, 
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Left eigenvector bad! Off by: " \
                       + str(la.norm(self.EpsL(self.l) - self.l))
                       
            if not sp.allclose(self.EpsR(self.r), self.r,
            rtol=self.itr_rtol*self.check_fac,
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Right eigenvector bad! Off by: " \
                       + str(la.norm(self.EpsR(self.r) - self.r))
            
            if not sp.allclose(self.l, m.H(self.l),
            rtol=self.itr_rtol*self.check_fac, 
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: l is not hermitian!"

            if not sp.allclose(self.r, m.H(self.r),
            rtol=self.itr_rtol*self.check_fac, 
            atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: r is not hermitian!"
            
            if not sp.all(la.eigvalsh(self.l) > 0):
                print "Sanity check failed: l is not pos. def.!"
                
            if not sp.all(la.eigvalsh(self.r) > 0):
                print "Sanity check failed: r is not pos. def.!"
            
            norm = myVVop(self.l, self.r, out=tmp)
            if not sp.allclose(norm, 1.0, atol=1E-13, rtol=0):
                print "Sanity check failed: Bad norm = " + str(norm)
    
    def Restore_CF(self, force_r_CF=False):      
        M = sp.zeros_like(self.r)
        for s in xrange(self.q):
            M += m.matmul(None, self.A[s], m.H(self.A[s]))
        
        G = m.H(la.cholesky(self.r))
        G_i = m.invtr(G, lower=True)
        
        for s in xrange(self.q):
            m.matmul(self.A[s], G_i, self.A[s], G)
            
        m.matmul(self.l, m.H(G), self.l, G)
        self.r[:] = sp.eye(self.D)
            
        self.Calc_lr(force_r_CF=True)
        
        if self.sanity_checks:
            M.fill(0)
            for s in xrange(self.q):
                M += m.matmul(None, self.A[s], m.H(self.A[s]))            
                
            if not sp.allclose(M, sp.eye(M.shape[0])) or not sp.allclose(self.r,
            sp.eye(self.D)):
                print "Sanity check failed: Could not achieve R-CF."

        if self.symm_gauge and not force_r_CF:
            sqrt_l = m.sqrtmh(self.l)
    
            G = m.sqrtmh(sqrt_l)
            G_i = la.inv(G)
            
            if self.sanity_checks and not sp.allclose(
                m.matmul(None, G_i, G_i, G_i, G_i, self.l), sp.eye(self.D)):
                    print "Sanity check failed: 4th root of l is bad!"
            
            for s in xrange(self.q):
                m.matmul(self.A[s], G, self.A[s], G_i)
                
            self.l[:] = sqrt_l
            self.r[:] = self.l
            
            self.Calc_lr()
            
            if self.sanity_checks:
                if not sp.allclose(self.l, self.r):
                    print "Sanity check failed: Could not achieve S-CF."
    
    def Calc_C(self):
        if not self.h_nn_cptr is None:
            self.C = tc.calc_C(self.A, self.A, self.h_nn_cptr, self.C)
        else:
            self.C.fill(0)
            
            AA = sp.empty_like(self.A[0])
            
            for (u, v) in sp.ndindex(self.q, self.q):
                m.matmul(AA, self.A[u], self.A[v])
                for (s, t) in sp.ndindex(self.q, self.q):
                    h = self.h_nn(s, t, u, v) #for large q, this executes a lot..
                    if h != 0:
                        self.C[s, t] += h * AA
    
    def Calc_K(self):
        Hr = sp.zeros_like(self.A[0])
        
        for (s, t) in sp.ndindex(self.q, self.q):
            Hr += m.matmul(None, self.C[s, t], self.r, m.H(self.A[t]), 
                           m.H(self.A[s]))
        
        self.h = myVVop(self.l, Hr, out=self.tmp)
        
        QHr = Hr - self.r * self.h
        
        op = KsuperOP(self.l, self.r, self.A)
        
        self.K = self.K.reshape((self.D**2))
        QHr = QHr.reshape((self.D**2))
        
        self.K, info = las.bicgstab(op, QHr, x0=self.K, maxiter=1000, 
                                    tol=self.itr_rtol)
        
        if info > 0:
            print "Warning: Did not converge on solution for K!"
        
        #Test
        if self.sanity_checks:
            RHS_test = op.matvec(self.K)
            if not sp.allclose(RHS_test, QHr, rtol=self.itr_rtol*self.check_fac,
                                atol=self.itr_atol*self.check_fac):
                print "Sanity check failed: Bad K solution! Off by: " + str(
                        la.norm(RHS_test - QHr))
        
        self.K = self.K.reshape((self.D, self.D))
            
    def Calc_Vsh(self, r_sqrt):
        R = sp.zeros((self.D, self.q, self.D), dtype=self.typ, order='C')
        
        for s in xrange(self.q):
            R[:,s,:] = m.matmul(None, r_sqrt, m.H(self.A[s]))
        
        R = R.reshape((self.q * self.D, self.D))
        
        V = m.H(ns.nullspace_qr(m.H(R))) 
        #R can be pretty huge for large q and D. The decomp. can take a long time...

#        print "V Checks..."
#        print sp.allclose(sp.dot(V, m.H(V)), sp.eye(self.q*self.D - self.D))
#        print sp.allclose(sp.dot(V, R), 0)
        V = V.reshape(((self.q - 1) * self.D, self.D, self.q)) 
        
        #prepare for using V[s] and already take the adjoint, since we use it more often
        Vsh = sp.empty((self.q, self.D, (self.q - 1) * self.D), dtype=self.typ, 
                       order=self.odr)
        for s in xrange(self.q):
            Vsh[s] = m.H(V[:,:,s])
        
        return Vsh
        
    def Calc_x(self, l_sqrt, l_sqrt_i, r_sqrt, r_sqrt_i, Vsh, out=None):
        if out is None:
            out = sp.zeros((self.D, (self.q - 1) * self.D), dtype=self.typ, 
                           order=self.odr)
        
        tmp = sp.zeros_like(out)
        tmp2 = sp.zeros_like(self.A[0])
        for s in xrange(self.q):
            tmp += m.matmul(None, self.A[s], self.K, r_sqrt_i, Vsh[s])
            
            tmp2.fill(0)
            for t in xrange(self.q):
                tmp2 += m.matmul(None, self.C[s, t], self.r, m.H(self.A[t]))
            tmp += m.matmul(None, tmp2, r_sqrt_i, Vsh[s])
            
        out += sp.dot(l_sqrt, tmp)
        
        tmp.fill(0)
        for s in xrange(self.q):
            tmp2.fill(0)
            for t in xrange(self.q):
                tmp2 += m.matmul(None, m.H(self.A[t]), self.l, self.C[t, s])
            tmp += m.matmul(None, tmp2, r_sqrt, Vsh[s])
        out += sp.dot(l_sqrt_i, tmp)
        
        return out
        
    def B_from_x(self, x, Vsh, l_sqrt_i, r_sqrt_i, out=None):
        if out is None:
            out = sp.zeros_like(self.A)
            
        for s in xrange(self.q):
            m.matmul(out[s], l_sqrt_i, x, m.H(Vsh[s]), r_sqrt_i)
            
        return out
        
    def Calc_B(self):
        #print "sqrts and inverses start"
        l_sqrt = m.sqrtmh(self.l)
        l_sqrt_i = la.inv(l_sqrt)
        r_sqrt = m.sqrtmh(self.r)
        r_sqrt_i = la.inv(r_sqrt)
        #print "sqrts and inverses stop"
        
        if self.sanity_checks:
            if not sp.allclose(sp.dot(l_sqrt, l_sqrt), self.l):
                print "Sanity check failed: l_sqrt is bad!"
            if not sp.allclose(sp.dot(l_sqrt, l_sqrt_i), sp.eye(self.D)):
                print "Sanity check failed: l_sqrt_i is bad!"
            if not sp.allclose(sp.dot(r_sqrt, r_sqrt), self.r):
                print "Sanity check failed: l_sqrt is bad!"
            if not sp.allclose(sp.dot(r_sqrt, r_sqrt_i), sp.eye(self.D)):
                print "Sanity check failed: l_sqrt_i is bad!"
                
        #print "Vsh start"
        Vsh = self.Calc_Vsh(r_sqrt)
        #print "Vsh stop"
        
        #print "x start"
        x = self.Calc_x(l_sqrt, l_sqrt_i, r_sqrt, r_sqrt_i, Vsh)
        #print "x stop"
        
        self.eta = sp.sqrt(myVVop(x, x))

        return self.B_from_x(x, Vsh, l_sqrt_i, r_sqrt_i)
        
    def TakeStep(self, dtau, B=None):
        
        if B is None:
            B = self.Calc_B()
        
        if self.sanity_checks:
            #Test gauge-fixing:
            tst = sp.zeros_like(self.l)
            for s in xrange(self.q):
                tst += m.matmul(None, B[s], self.r, m.H(self.A[s]))
            if not sp.allclose(tst, 0):
                print "Sanity check failed: Gauge-fixing violation!"
        
        for s in xrange(self.q):
            self.A[s] += -dtau * B[s]
            
    def Expect_SS(self, op):
        Or = self.EpsR(self.r, op=op)
        
        return myVVop(self.l, Or)
            
    def Expect_2S(self, op):
        AAuv = sp.empty_like(self.A[0])
        res = sp.zeros_like(self.r)
        
        for (u, v) in sp.ndindex(self.q, self.q):
            m.matmul(AAuv, self.A[u], self.A[v])
            for (s, t) in sp.ndindex(self.q, self.q):
                res += op(u, v, s, t) * m.matmul(None, self.A[s], self.A[t], 
                                                       self.r, m.H(AAuv))
        
        return myVVop(self.l, res)
        
    def Density_SS(self):
        rho = sp.empty((self.q, self.q), dtype=self.typ)
        for (s, t) in sp.ndindex(self.q, self.q):
            m.matmul(self.tmp, self.A[t], self.r, m.H(self.A[s]))
            rho[s, t] = myVVop(self.l, self.tmp)
        return rho
            
    def SaveState(self, file):
        tosave = sp.empty((4), dtype=sp.ndarray)
        tosave[0] = self.A
        tosave[1] = self.l
        tosave[2] = self.r
        tosave[3] = self.K
        sp.save(file, tosave)
        
    def LoadState(self, file, expand=False):
        state = sp.load(file)
        
        newA = state[0]
        newl = state[1]
        newr = state[2]
        newK = state[3]
        
        if (newA.shape == self.A.shape) and (newl.shape == self.l.shape) and (
        newr.shape == self.r.shape) and (newK.shape == self.K.shape):
            self.A[:] = newA
            self.l[:] = newl
            self.r[:] = newr
            self.K[:] = newK
            return True
        elif expand and (len(newA.shape) == 2) and (newA.shape[0] == 
        self.A.shape[0]) and (newA.shape[1] == newA.shape[2]) and (newA.shape[1]
        <= self.A.shape[1]):
            D = newA.shape[1]
            self.A[:, 0:D, 0:D] = newA #TODO: Change this...
        else:
            return False
            
    def Expand_D(self, newD):
        if newD < self.D:
            return False
        
        oldD = self.D
        oldA = self.A
        oldl = self.l
        oldr = self.r
        oldK = self.K
        
        self._init_arrays(newD, self.q)
        
        norm = la.norm(oldA)
        fac = (norm / (self.q * oldD**2))
#        m.randomize_cmplx(newA[:, self.D:, self.D:], a=-fac, b=fac)
        m.randomize_cmplx(self.A[:, :oldD, oldD:], a=-fac, b=fac)
        m.randomize_cmplx(self.A[:, oldD:, :oldD], a=-fac, b=fac)
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
        
    def Fuzz_State(self, f=1.0):
        norm = la.norm(self.A)
        fac = f*(norm / (self.q * self.D**2))        
        
        R = sp.empty_like(self.A)
        m.randomize_cmplx(R, -fac/2.0, fac/2.0)
        
        self.A += R