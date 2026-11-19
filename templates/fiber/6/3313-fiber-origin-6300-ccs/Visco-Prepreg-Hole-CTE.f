      SUBROUTINE UMAT(STRESS,STATEV,DDSDDE,SSE,SPD,SCD,
     + RPL,DDSDDT,DRPLDE,DRPLDT,
     + STRAN,DSTRAN,TIME,DTIME,TEMP,DTEMP,PREDEF,DPRED,CMNAME,
     + NDI,NSHR,NTENS,NSTATV,PROPS,NPROPS,COORDS,DROT,PNEWDT,
     + CELENT,DFGRD0,DFGRD1,NOEL,NPT,LAYER,KSPT,JSTEP,KINC)
C
      INCLUDE 'ABA_PARAM.INC'
C
      CHARACTER*80 CMNAME


      DIMENSION STRESS(NTENS),STATEV(NSTATV),DDSDDE(NTENS,NTENS),
     + DDSDDT(NTENS),DRPLDE(NTENS),STRAN(NTENS),DSTRAN(NTENS),
     + TIME(2),PREDEF(1),DPRED(1),PROPS(NPROPS),COORDS(3),
     + DROT(3,3),DFGRD0(3,3),DFGRD1(3,3),JSTEP(4)
      DOUBLE PRECISION D11(100),D12(100),D13(100),D22(100),D23(100),
     + D33(100),D44(100),D55(100),D66(100),C_0(NTENS,NTENS),
     + C_INF(NTENS,NTENS),C_DEF(NTENS,NTENS),STREND(NTENS)

C --- D11 ---
      D11(1) = 1.1231884060e+04
      D11(2) = 1.4975845410e+03


C --- D12 ---
      D12(1) = 6.8840579710e+03
      D12(2) = 9.1787439610e+02


C --- D13 ---
      D13(1) = 6.8840579710e+03
      D13(2) = 9.1787439610e+02


C --- D22 ---
      D22(1) = 1.1231884060e+04
      D22(2) = 1.4975845410e+03


C --- D23 ---
      D23(1) = 6.8840579710e+03
      D23(2) = 9.1787439610e+02


C --- D33 ---
      D33(1) = 1.1231884060e+04
      D33(2) = 1.4975845410e+03


C --- D44 ---
      D44(1) = 2.1739130430e+03
      D44(2) = 2.8985507250e+02


C --- D55 ---
      D55(1) = 2.1739130430e+03
      D55(2) = 2.8985507250e+02


C --- D66 ---
      D66(1) = 2.1739130430e+03
      D66(2) = 2.8985507250e+02

C Calculate the number of increment
        n = INT(TIME(2)+1)
C Calculate the instaneous stiffness matrix
        C_0=0
        C_0(1,1) = D11(n)
        C_0(2,2) = D22(n)
        C_0(3,3) = D33(n)
        C_0(4,4) = D44(n)
        C_0(5,5) = D55(n)
        C_0(6,6) = D66(n)
        C_0(1,2) = D12(n)
        C_0(1,3) = D13(n)
        C_0(2,3) = D23(n)
        C_0(2,1) = C_0(1,2)
        C_0(3,1) = C_0(1,3)
        C_0(3,2) = C_0(2,3)
C Update mechenical strain
        STREND = STRAN + DSTRAN
C Define Jacobi Stiffness Matrix
        DDSDDE = C_0
C Update Stress in new increment
        STRESS = MATMUL(DDSDDE,STREND)
C
       RETURN
       END


       SUBROUTINE UEXPAN(EXPAN,DEXPANDT,TEMP,TIME,DTIME,PREDEF,
     1 DPRED,STATEV,CMNAME,NSTATV,NOEL)
C
       INCLUDE 'ABA_PARAM.INC'
C
       CHARACTER*80 CMNAME
C
       DIMENSION EXPAN(*),DEXPANDT(*),TEMP(2),TIME(2),PREDEF(*),
     1 DPRED(*),STATEV(NSTATV)
       DOUBLE PRECISION CTE_X(100),CTE_Y(100),CTE_Z(100)
C
C --- CTE_X ---
      CTE_X(1) = 1.48e-04
      CTE_X(2) = 1.48e-04


C --- CTE_Y ---
      CTE_Y(1) = 1.48e-04
      CTE_Y(2) = 1.48e-04


C --- CTE_Z ---
      CTE_Z(1) = 1.48e-04
      CTE_Z(2) = 1.48e-04


C 
       m = INT(TIME(2))
C Update the thermal expansion strain
       IF (m.EQ.1)THEN
          EXPAN(1) = CTE_X(m)*TEMP(1)
          EXPAN(2) = CTE_Y(m)*TEMP(1)
          EXPAN(3) = CTE_Z(m)*TEMP(1)
       ELSE
          EXPAN(1) = (CTE_X(m)-CTE_X(m-1))*TEMP(1)
          EXPAN(2) = (CTE_Y(m)-CTE_Y(m-1))*TEMP(1)
          EXPAN(3) = (CTE_Z(m)-CTE_Z(m-1))*TEMP(1)
       END IF
       RETURN
       END  
