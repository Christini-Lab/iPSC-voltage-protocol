% function [Vm, I_tot, Yc,Cai,Nai, caSR,t]=funz_main_Paci2018()
options = odeset('MaxStep',1e-3,'InitialStep',2e-5);
    %% SS originale
%      Y=[     -0.070  0.32    0.0002  0    0    1     1     1      0      1      0   0.75  0.75  0   0.1    1    0    9.2    0     0.75    0.3     0.9     0.1];
% YNames = {'Vm', 'Ca_SR', 'Cai', 'g', 'd', 'f1', 'f2', 'fCa', 'Xr1', 'Xr2', 'Xs', 'h', 'j', 'm', 'Xf', 'q', 'r', 'Nai', 'm_L', 'h_L', 'RyRa', 'RyRo', 'RyRc'};
% YUnits = {'V',   'mM',   'mM',  '-', '-', '-',  '-',  '-',   '-',   '-',   '-',  '-', '-', '-', '-',  '-', '-', 'mM',   '-',   '-',    '-',    '-',    '-'};

% %% SS a 800 
Y = [-0.0749228904740065 0.0936532528714175 3.79675694306440e-05 0 8.25220533963093e-05 0.741143500777858 0.999983958619179 0.997742015033076 0.266113517200784 0.434907203275640 0.0314334976383401 0.745356534740988 0.0760523580322096 0.0995891726023512 0.0249102482276486 0.841714924246004 0.00558005376429710 8.64821066193476 0.00225383437957339 0.0811507312565017 0.0387066722172937 0.0260449185736275 0.0785849084330126];
tic
%% Current blockers
tDrugApplication = 10000;
INaFRedMed = 1;
ICaLRedMed = 1;
IKrRedMed  = 1;
IKsRedMed  = 1;

duration = [0 40];
  
[t,Yc] = ode15s(@Paci2018, duration, Y, options, tDrugApplication, INaFRedMed, ICaLRedMed, IKrRedMed, IKsRedMed);
Vm   = Yc(:,1);
dVm  = [0; diff(Vm)./diff(t)];
caSR = Yc(:,2);
Cai  = Yc(:,3);
Nai  = Yc(:,18);

for i= 1:size(Yc,1)
[~, dati]    = Paci2018(t(i), Yc(i,:), tDrugApplication, INaFRedMed, ICaLRedMed, IKrRedMed, IKsRedMed);
    INa(i)   = dati(1);
    If(i)    = dati(2);
    ICaL(i)   = dati(3);
    Ito(i)   = dati(4);
    IKs(i)   = dati(5);
    IKr(i)   = dati(6);
    IK1(i)   = dati(7);
    INaCa(i) = dati(8);
    INaK(i)  = dati(9);
    IpCa(i)  = dati(10);
    IbNa(i)  = dati(11);
    IbCa(i)  = dati(12);
    Irel(i)  = dati(13);
    Iup(i)   = dati(14);
    Ileak(i) = dati(15); 
    Istim(i) = dati(16);
    E_K(i)   = dati(17);
    E_Na(i)  = dati(18);
    INaL(i)  = dati(19);
end
result       = [INa; If; ICaL; Ito; IKs; IKr; IK1; INaCa; INaK; IpCa; IbNa; IbCa; Irel; Iup; Ileak; Istim; E_K; E_Na; INaL];
mat_correnti = [INa; If; ICaL; Ito; IKs; IKr; IK1; INaCa; INaK; IpCa; IbNa; IbCa; Irel; Iup; Ileak; Istim; INaL];
I_tot=sum(mat_correnti);
toc

plot(t, Yc(:,1))
hold on

yPython = csvread('pythonSolutionJit2.csv');
tPython = csvread('pySolutionTimeJit2.csv');

plot(tPython, yPython(:,1))
xlim([0 10])
xlabel('Time (s)', 'FontSize', 14)
ylabel('Potential (mV)', 'FontSize', 14)
title('Paci Matlab and Python', 'FontSize', 18)
legend('Matlab', 'Python', 'FontSize', 14)


