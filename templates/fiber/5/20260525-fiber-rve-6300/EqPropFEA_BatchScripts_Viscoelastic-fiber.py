# -*- coding: utf-8 -*-
from abaqus import *
from abaqusConstants import *
from caeModules import *
from odbAccess import *
from textRepr import *
import numpy as np
import sys
import time
import __main__
import math
import os
import ctypes
import multiprocessing
import os, glob

instanceName = 'Part-1-1'
L = 7.12E-03
H = 7.12E-03
W = 7.12E-03
Dispx = L*0.2
Dispy = H*0.2
Dispz = W*0.2


CPUs = 2
inpname = 'job-E11.inp'
jobname = 'job-E11'
NameOdb = 'job-E11.odb'
subname= 'Visco-Prepreg-Hole.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()

odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
StartRow = 0
total_rows = total_frames - 1
total_cols = 12   # 12个性能
data = np.zeros((total_rows, total_cols))
########## NodeSet ##########
nodeSet_RP4 = odb.rootAssembly.nodeSets['RP4']
nodeSet_C1 = odb.rootAssembly.nodeSets['C1']
nodeSet_C2 = odb.rootAssembly.nodeSets['C2']
nodeSet_C5 = odb.rootAssembly.nodeSets['C5']
nodeSet_C4 = odb.rootAssembly.nodeSets['C4']
coordC1 = nodeSet_C1.nodes[0][0].coordinates
coordC2 = nodeSet_C2.nodes[0][0].coordinates
coordC5 = nodeSet_C5.nodes[0][0].coordinates
coordC4 = nodeSet_C4.nodes[0][0].coordinates
########## 主循环 ##########
for frame_id in range(1, total_frames):
    frame = step.frames[frame_id]
    row_index = frame_id - 1
    ########## 计算 E11 ##########
    fieldOutRF = frame.fieldOutputs['RF']
    RF_RP4 = fieldOutRF.getSubset(region=nodeSet_RP4, position=NODAL)
    force11 = 0
    for RF in RF_RP4.values:
        force11 += RF.data[0]
    stress11_E11 = abs(force11/(W*H))
    strain11_E11 = Dispx/L
    E11 = stress11_E11/strain11_E11
    ########## 位移 ##########
    fieldOutU = frame.fieldOutputs['U']
    U_C1 = fieldOutU.getSubset(region=nodeSet_C1, position=NODAL)
    U_C2 = fieldOutU.getSubset(region=nodeSet_C2, position=NODAL)
    U_C5 = fieldOutU.getSubset(region=nodeSet_C5, position=NODAL)
    U_C4 = fieldOutU.getSubset(region=nodeSet_C4, position=NODAL)
    C1U1 = U_C1.values[0].data[0]
    C1U2 = U_C1.values[0].data[1]
    C1U3 = U_C1.values[0].data[2]
    C2U1 = U_C2.values[0].data[0]
    C5U2 = U_C5.values[0].data[1]
    C4U3 = U_C4.values[0].data[2]
    ########## 计算 V12 ##########
    C1U1new = coordC1[0] + C1U1
    C2U1new = coordC2[0] + C2U1
    Dis1 = abs(C1U1new - C2U1new)
    E11U1 = abs(L - Dis1)
    C1U2new = coordC1[1] + C1U2
    C5U2new = coordC5[1] + C5U2
    Dis2 = abs(C1U2new - C5U2new)
    E11U2 = abs(H - Dis2)
    V12 = (E11U2/H)/(E11U1/L)
    ########## 计算 V13 ##########
    C1U3new = coordC1[2] + C1U3
    C4U3new = coordC4[2] + C4U3
    Dis3 = abs(C1U3new - C4U3new)
    E11U3 = abs(W - Dis3)
    V13 = (E11U3/W)/(E11U1/L)
    data[row_index,0] = E11
    data[row_index,3] = V12
    data[row_index,4] = V13
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()

###### Elastic modulus E22 ######
inpname = 'job-E22.inp'
jobname = 'job-E22'
NameOdb = 'job-E22.odb'
subname= 'Visco-Prepreg-Hole.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()

odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
total_rows = total_frames - 1
total_cols = 12
nodeSet_RP5 = odb.rootAssembly.nodeSets['RP5']
data = np.loadtxt('Mechanical_constants.txt', ndmin=2)
for frame_id in range(1, total_frames):
    frame = step.frames[frame_id]
    row_index = frame_id - 1
    fieldOutRF = frame.fieldOutputs['RF']
    RF_RP5 = fieldOutRF.getSubset(region=nodeSet_RP5, position=NODAL)
    force22 = 0
    for RF in RF_RP5.values:
        force22 += RF.data[1]
    stress22_E22 = abs(force22/(W*L))
    strain22_E22 = Dispy/H
    E22 = stress22_E22/strain22_E22
    data[row_index,1] = E22
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()

nodeSet_C1 = odb.rootAssembly.nodeSets['C1']
nodeSet_C5 = odb.rootAssembly.nodeSets['C5']
nodeSet_C4 = odb.rootAssembly.nodeSets['C4']
coordC1 = nodeSet_C1.nodes[0][0].coordinates
coordC5 = nodeSet_C5.nodes[0][0].coordinates
coordC4 = nodeSet_C4.nodes[0][0].coordinates
total_frames = len(step.frames)
for frame_id in range(1, total_frames):
    frame = step.frames[frame_id]
    row_index = frame_id - 1
    fieldOutU = frame.fieldOutputs['U']
    U_C1 = fieldOutU.getSubset(region=nodeSet_C1, position=NODAL)
    U_C4 = fieldOutU.getSubset(region=nodeSet_C4, position=NODAL)
    U_C5 = fieldOutU.getSubset(region=nodeSet_C5, position=NODAL)
    C1U2 = U_C1.values[0].data[1]
    C5U2 = U_C5.values[0].data[1]
    C1U3 = U_C1.values[0].data[2]
    C4U3 = U_C4.values[0].data[2]
    C1U2new = coordC1[1] + C1U2
    C5U2new = coordC5[1] + C5U2
    C1U3new = coordC1[2] + C1U3
    C4U3new = coordC4[2] + C4U3
    Dis2 = abs(C1U2new - C5U2new)
    Dis3 = abs(C1U3new - C4U3new)
    E22U2 = abs(H - Dis2)
    E22U3 = abs(W - Dis3)
    V23 = (E22U3 / W) / (E22U2 / H)
    data[row_index,5] = V23
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()

###### Elastic modulus E33 ######
inpname = 'job-E33.inp'
jobname = 'job-E33'
NameOdb = 'job-E33.odb'
subname= 'Visco-Prepreg-Hole.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()

odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
total_rows = total_frames - 1
total_cols = 12
data = np.loadtxt('Mechanical_constants.txt', ndmin=2)
nodeSet_RP6 = odb.rootAssembly.nodeSets['RP6']
for i in range(1, total_frames):
    frame = step.frames[i] 
    row_index = i - 1
    fieldOutRF = frame.fieldOutputs['RF']
    RF_RP6 = fieldOutRF.getSubset(region=nodeSet_RP6,position=NODAL)
    force33 = 0
    for RF in RF_RP6.values:
        force33 = force33 + RF.data[2]
    stress33_E33 = abs(force33/(H*L))
    strain33_E33 = Dispz/W        
    E33 = stress33_E33/strain33_E33
    data[row_index,2] = E33
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()


###### Elastic modulus G12 ######

inpname = 'job-G12.inp'
jobname = 'job-G12'
NameOdb = 'job-G12.odb'
subname= 'Visco-Prepreg-Hole.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()


odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
total_rows = total_frames - 1
total_cols = 12
data = np.loadtxt('Mechanical_constants.txt', ndmin=2)
nodeSet_RP4 = odb.rootAssembly.nodeSets['RP4']
total_frames = len(odb.steps['Step-1'].frames)
for i in range(1, total_frames):
    frame = step.frames[i] 
    row_index = i - 1
    fieldOutRF = frame.fieldOutputs['RF']
    RF_RP4 = fieldOutRF.getSubset(region=nodeSet_RP4,position=NODAL)
    force12 = 0
    for RF in RF_RP4.values:
        force12 = force12 + RF.data[0]
    stress12_G12 = abs(force12/(W*L))
    strain12_G12 =  ((Dispx/H)+(Dispy/L))        
    G12=stress12_G12/strain12_G12
    data[row_index,6] = G12
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()


###### Elastic modulus G13 ######

inpname = 'job-G13.inp'
jobname = 'job-G13'
NameOdb = 'job-G13.odb'
subname= 'Visco-Prepreg-Hole.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()

odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
total_rows = total_frames - 1
total_cols = 12
data = np.loadtxt('Mechanical_constants.txt', ndmin=2)
nodeSet_RP5 = odb.rootAssembly.nodeSets['RP5']
nodeSet_RP6 = odb.rootAssembly.nodeSets['RP6']
total_frames = len(odb.steps['Step-1'].frames)
for i in range(1, total_frames):
    frame = step.frames[i] 
    row_index = i - 1
    fieldOutRF = frame.fieldOutputs['RF']
    RF_RP5 = fieldOutRF.getSubset(region=nodeSet_RP5,position=NODAL)
    force13 = 0
    for RF in RF_RP5.values:
        force13 = force13 + RF.data[0]
    stress13_G13 = abs(force13/(H*L))
    strain13_G13 = ((Dispx/W)+(Dispz/L)) 
    G13 = stress13_G13/strain13_G13
    data[row_index,7] = G13
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()



###### Elastic modulus G23 ######

inpname = 'job-G23.inp'
jobname = 'job-G23'
NameOdb = 'job-G23.odb'
subname= 'Visco-Prepreg-Hole.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()

odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
total_rows = total_frames - 1
total_cols = 12
data = np.loadtxt('Mechanical_constants.txt', ndmin=2)
nodeSet_RP6 = odb.rootAssembly.nodeSets['RP6']
total_frames = len(odb.steps['Step-1'].frames)
for i in range(1, total_frames):
    frame = step.frames[i] 
    row_index = i - 1
    fieldOutRF = frame.fieldOutputs['RF']
    RF_RP6 = fieldOutRF.getSubset(region=nodeSet_RP6,position=NODAL)
    force23 = 0
    for RF in RF_RP6.values:
        force23 = force23 + RF.data[1]
    stress23_G23 = abs(force23/(L*H))
    strain23_G23 =  ((Dispy/W)+(Dispz/H))
    G23 = stress23_G23/strain23_G23
    data[row_index,8] = G23
    f_mech = open('Mechanical_constants.txt','w')
    for row in range(total_rows):
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            data[row,0],
            data[row,1],
            data[row,2],
            data[row,3],
            data[row,4],
            data[row,5],
            data[row,6],
            data[row,7],
            data[row,8],
            data[row,9],
            data[row,10],
            data[row,11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()



###### Coefficient of Thermal Expansion CTE ######

fntemp = 100.
intemp = 0.

# 打开 ODB
inpname = 'job-CTE.inp'
jobname = 'job-CTE'
NameOdb = 'job-CTE.odb'
subname= 'Visco-Prepreg-Hole-CTE.f'

mdb.JobFromInputFile(name=jobname, inputFileName=inpname, 
                    type=ANALYSIS, atTime=None, waitMinutes=0, waitHours=0, queue=None, 
                    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True, 
                    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE, userSubroutine=subname, 
                    scratch='', resultsFormat=ODB, numThreadsPerMpiProcess=1, multiprocessingMode=DEFAULT,  
                    numCpus=CPUs, numDomains=2, numGPUs=0)
mdb.jobs[jobname].submit(consistencyChecking=OFF)
mdb.jobs[jobname].waitForCompletion()

odb = openOdb(NameOdb, readOnly=True)
step = odb.steps['Step-1']
total_frames = len(step.frames)
total_rows = total_frames - 1  # 实际行数

# 读取已有数据
try:
    data_old = np.loadtxt('Mechanical_constants.txt')
    if data_old.ndim == 1:
        data_old = data_old.reshape(-1, data_old.size)
except:
    data_old = np.zeros((0, 12))

# 确保列数为12
if data_old.shape[1] < 12:
    extra_cols = 12 - data_old.shape[1]
    data_old = np.hstack((data_old, np.zeros((data_old.shape[0], extra_cols))))

# 扩展行到 total_rows，如果原来行数不够，用零填充
if data_old.shape[0] < total_rows:
    extra_rows = total_rows - data_old.shape[0]
    data = np.vstack((data_old, np.zeros((extra_rows, 12))))
else:
    data = data_old.copy()

# NodeSets
nodeSet_C1 = odb.rootAssembly.nodeSets['C1']
nodeSet_C2 = odb.rootAssembly.nodeSets['C2']
nodeSet_C4 = odb.rootAssembly.nodeSets['C4']
nodeSet_C5 = odb.rootAssembly.nodeSets['C5']

# 遍历帧，计算 CTE
for i in range(1, total_frames):
    frame = step.frames[i]
    row_index = i - 1
    fieldOutU = frame.fieldOutputs['U']
    U_C1 = fieldOutU.getSubset(region=nodeSet_C1, position=NODAL)
    U_C2 = fieldOutU.getSubset(region=nodeSet_C2, position=NODAL)
    U_C4 = fieldOutU.getSubset(region=nodeSet_C4, position=NODAL)
    U_C5 = fieldOutU.getSubset(region=nodeSet_C5, position=NODAL)
    C1U1new = 0.0
    for u in U_C1.values:
        C1U1new += u.data[0]
    C2U1new = 0.0
    for u in U_C2.values:
        C2U1new += u.data[0]
    DeltaX = abs(C1U1new - C2U1new)
    CTE_X = DeltaX / (L * (fntemp - intemp))
    C1U2new = 0.0
    for u in U_C1.values:
        C1U2new += u.data[1]
    C5U2new = 0.0
    for u in U_C5.values:
        C5U2new += u.data[1]
    DeltaY = abs(C1U2new - C5U2new)
    CTE_Y = DeltaY / (H * (fntemp - intemp))
    C1U3new = 0.0
    for u in U_C1.values:
        C1U3new += u.data[2]
    C4U3new = 0.0
    for u in U_C4.values:
        C4U3new += u.data[2]
    DeltaZ = abs(C1U3new - C4U3new)
    CTE_Z = DeltaZ / (W * (fntemp - intemp))
    data[row_index, 9] = CTE_X
    data[row_index, 10] = CTE_Y
    data[row_index, 11] = CTE_Z
    f_mech = open('Mechanical_constants.txt', 'w')
    for row in range(data.shape[0]):
        row_data = np.zeros(12)
        ncols = min(12, data.shape[1])
        for c in range(ncols):
            row_data[c] = data[row, c]
        line = "%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\t%e\n" % (
            row_data[0],
            row_data[1],
            row_data[2],
            row_data[3],
            row_data[4],
            row_data[5],
            row_data[6],
            row_data[7],
            row_data[8],
            row_data[9],
            row_data[10],
            row_data[11]
        )
        f_mech.write(line)
    f_mech.close()
odb.close()