.section	.rdata
	.align	2
k_barrelGetFacingSideUnit:
	.word	0x3f800000		# 1 (float)
	.globl	k_BarrelGetfacingsideData
	.align	2
k_BarrelGetfacingsideData:
	.word	0x3fc00000		# 1.5 (float)
	.globl	k_barrelGetFacingSideThreshold
	.align	2
k_barrelGetFacingSideThreshold:
	.word	0x3f4ccccd		# 0.800000011921 (float)
	.globl	k_CbarrelUpdatetrackeraimData
	.align	2
k_CbarrelUpdatetrackeraimData:
	.word	0x424ccccc		# 51.1999969482 (float)
	.word	0x424ccccc		# 51.1999969482 (float)
	.word	0x40490fdb		# 3.14159274101 (float)
	.word	0x7f7fffff		# 3.40282346639e+38 (float)
	.globl	g_barrelFlaggedEffectRandomRanges
	.align	2
g_barrelFlaggedEffectRandomRanges:
	.word	0x43e10000		# 450 (float)
	.word	0x44160000		# 600 (float)
	.word	0x43e10000		# 450 (float)
	.word	0x44160000		# 600 (float)
	.globl	g_goalBesideActorClearance
	.align	2
g_goalBesideActorClearance:
	.word	0x424ccccc		# 51.1999969482 (float)
	.globl	g_trackGoalTargetHeightFactor
	.align	2
g_trackGoalTargetHeightFactor:
	.word	0x3f666666		# 0.899999976158 (float)
	.globl	k_barrelTrackGoalApproachPadding
	.align	2
k_barrelTrackGoalApproachPadding:
	.word	0x42cccccc		# 102.399993896 (float)
	.globl	k_barrelClearAimSearchRange
	.align	2
k_barrelClearAimSearchRange:
	.word	0x3fc00000		# 1.5 (float)
