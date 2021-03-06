#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
A demonstration of evoMPS by simulation of quench dynamics
for the transverse Ising model.

@author: Ashley Milsted
"""

import scipy as sp
import scipy.linalg as la
import matplotlib.pyplot as plt

import evoMPS.tdvp_uniform as tdvp

"""
First, we define our Hamiltonian and some observables.
"""

def h_ext(s, t):
    """The single-site Hamiltonian representing the external field.
    
    -h * sigmaX_s,t.
    
    The global variable h determines the strength.
    """
    if s == t:
        return 0
    else:
        return -h        

def h_nn(s, t, u, v):
    """The nearest neighbour Hamiltonian representing the interaction.

    -J * sigmaZ_n_s,t * sigmaZ_n+1_u,v.
    
    The global variable J determines the strength.
    """
    res = 0
    
    if s == u and t == v:
        res = -J * (-1)**s * (-1)**t
        
    if s != u and t == v:
        res += -h
        
#    if t == v:
#        res += 0
#    else:
#        res += -h
        
    return res
    
def z_ss(s, t):
    """Spin observable: z-direction
    """
    if s == t:
        return (-1)**s
    else:
        return 0
        
def x_ss(s, t):
    """Spin observable: x-direction
    """
    if s == t:
        return 0
    else:
        return 1
        
def y_ss(s, t):
    """Spin observable: y-direction
    """
    if s == t:
        return 0
    else:
        return 1.j * (-1)**t

"""
Next, we set up some global variables to be used as parameters to 
the evoMPS class.
"""

D = 8 #The bond dimension
q = 2 #The site dimension

"""
Now we are ready to create an instance of the evoMPS class.
"""
s = tdvp.EvoMPS_TDVP_Uniform(D, q)

"""
Tell evoMPS about our Hamiltonian.
"""
s.h_nn = h_nn

"""
Set the initial Hamiltonian parameters.
"""
h = 0.7
J = 1.00

"""
We're going to simulate a quench after we find the ground state.
Set the new J parameter for the real time evolution here.
"""
J_real = 2

"""
Now set the step sizes for the imaginary and the real time evolution.
These are currently fixed.
"""
step = 0.1
realstep = 0.01

"""
Now set the tolerance for the imaginary time evolution.
When the state tolerance falls below this level, the
real time simulation of the quench will begin.
"""
tol_im = 1E-10
total_steps = 1000

"""
The following handles loading the ground state from a file.
The ground state will be saved automatically when it is declared found.
If this script is run again with the same settings, an existing
ground state will be loaded, if present.
"""
grnd_fname_fmt = "t_ising_uni_D%d_q%d_J%g_h%g_s%g_dtau%g_ground.npy"

grnd_fname = grnd_fname_fmt % (D, q, J, h, tol_im, step)

expand = False

if True:
    try:
       a_file = open(grnd_fname, 'rb')
       s.load_state(a_file)
       a_file.close
       real_time = not expand
       loaded = True
       print 'Using saved ground state: ' + grnd_fname
    except IOError as e:
       print 'No existing ground state could be opened.'
       real_time = False
       loaded = False
else:
    loaded = False
    real_time = False
    
s.sanity_checks = True
s.symm_gauge = False

if __name__ == "__main__":
    """
    Prepare some loop variables and some vectors to hold data from each step.
    """
    t = 0. + 0.j
    imsteps = 0
    
    reCF = []
    reNorm = []
    
    T = sp.zeros((total_steps), dtype=sp.complex128)
    E = sp.zeros((total_steps), dtype=sp.complex128)
    lN = sp.zeros((total_steps), dtype=sp.complex128)
    
    Sx = sp.zeros((total_steps), dtype=sp.complex128)
    Sy = sp.zeros((total_steps), dtype=sp.complex128)
    Sz = sp.zeros((total_steps), dtype=sp.complex128)
    
    Mx = sp.zeros((total_steps), dtype=sp.complex128)   #Magnetization in x-direction.
       
       
    """
    Print a table header.
    """
    print "Bond dimensions: " + str(s.D)
    print
    col_heads = ["Step", "t", "eta", "H", "dH", 
                 "sig_x", "sig_y", "sig_z", "entr.",
                 "Next step"] #These last three are for testing the midpoint method.
    print "\t".join(col_heads)
    print
    
    for i in xrange(total_steps):
        T[i] = t
        
        row = [str(i)]
        row.append(str(t))
        
        eta = s.eta.real
        row.append("%.4g" % eta)
        
        s.update()
        
        E[i] = s.h
        row.append("%.15g" % E[i].real)
        
        if i > 0:        
            dE = E[i].real - E[i - 1].real
        else:
            dE = E[i]
        
        row.append("%.2e" % (dE.real))
            
        """
        Compute obserables!
        """
        
        Sx[i] = s.expect_1s(x_ss) #Spin observables for site 3.
        Sy[i] = s.expect_1s(y_ss)
        Sz[i] = s.expect_1s(z_ss)
        row.append("%.3g" % Sx[i].real)
        row.append("%.3g" % Sy[i].real)
        row.append("%.3g" % Sz[i].real)
        
        entr = s.S_hc
        row.append("%.3g" % entr.real)
        
        """
        Switch to real time evolution if we have the ground state.
        """
        if expand and (loaded or (not real_time and i > 1 and eta < tol_im)):
            grnd_fname = grnd_fname_fmt % (D, q, J, h, tol_im, step)        
            
            if not loaded:
                s.save_state(grnd_fname)
            
            D = D * 2
            print "***MOVING TO D = " + str(D) + "***"
            s.expand_D(D)
            s.update()
            
            loaded = False
        elif loaded or (not real_time and i > 1 and eta < tol_im):
            real_time = True
            
            s.save_state(grnd_fname)
            J = J_real
            step = realstep * 1.j
            loaded = False
            print 'Starting real time evolution!'
        
        row.append(str(1.j * sp.conj(step)))
        
        """
        Carry out next step!
        """
        if not real_time:
            print "\t".join(row)
            s.take_step(step)
            imsteps += 1
        else:
            print "\t".join(row)
            s.take_step_RK4(step)
        
        t += 1.j * sp.conj(step)
    
    """
    Simple plots of the results.
    """
    
    if imsteps > 0: #Plot imaginary time evolution of K1 and Mx
        tau = T.imag[0:imsteps]
        
        fig1 = plt.figure(1)
        fig2 = plt.figure(2) 
        K1_tau = fig1.add_subplot(111)
        K1_tau.set_xlabel('tau')
        K1_tau.set_ylabel('H')
        M_tau = fig2.add_subplot(111)
        M_tau.set_xlabel('tau')
        M_tau.set_ylabel('M_x')    
        
        K1_tau.plot(tau, E.real[0:imsteps])
        M_tau.plot(tau, Sx.real[0:imsteps])
    
    #Now plot the real time evolution of K1 and Mx
    t = T.real[imsteps + 1:]
    fig3 = plt.figure(3)
    fig4 = plt.figure(4)
    
    K1_t = fig3.add_subplot(111)
    K1_t.set_xlabel('t')
    K1_t.set_ylabel('H')
    M_t = fig4.add_subplot(111)
    M_t.set_xlabel('t')
    M_t.set_ylabel('M_x')
    
    K1_t.plot(t, E.real[imsteps + 1:])
    M_t.plot(t, Sx.real[imsteps + 1:])
    
    plt.show()
