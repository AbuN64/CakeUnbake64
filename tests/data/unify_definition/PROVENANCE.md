# Census regression slice

RageWarsDecomp bc0d034ac, UNIFY-DEFINITION/classes.tsv and CLASH.tsv.
Paths, names and declared type shapes below are retained. Bodies and line numbers are reduced.
The function belongs to the module named by modules.tsv; its header already exists.
The data module has no header. LINK_DISPATCHMATCHINGTRIGGERS is a reduced headerless caller.
No real compiler or RageWars wave is run.

## classes.tsv

```tsv
name	class	definition_type	definition_at	declarations
Achieve_ApplyPlayerOptionOverrides	one-definition	void(struct PlayerLoadoutSelectionView*)	SOURCE/ACHIEVE/MATCH_QUALIFIES/achieve_match_setup.CPP:24	2
Cache_AllocResourceBlock	no-definition			3
g_Ovltab6EncodedWordsBlock6	one-definition	void*[]	SOURCE/DATA/WEAPON_STATE_TABLES/WEAPON_STATE_TABLES.CPP:18	2
```

## CLASH.tsv

```tsv
name	ntypes	declarations(type => file:line kind, versions)	users	status
Achieve_ApplyPlayerOptionOverrides	2	void(struct PlayerLoadoutSelectionView*) => SOURCE/ACHIEVE/MATCH_QUALIFIES/achieve_match_setup.CPP:24 fdef [de,eu,eu-x,us,us-rev1] | void(struct Player*) => SOURCE/AI/PLAYERAI/PLAYERAI__PLAYERAI.CPP:961 proto [de,eu,eu-x,us,us-rev1]	2: SOURCE/ACHIEVE/MATCH_QUALIFIES/achieve_match_setup.CPP SOURCE/AI/PLAYERAI/PLAYERAI__PLAYERAI.CPP	open
Cache_AllocResourceBlock	3	void*(int,void*,int) => SOURCE/ENGINE/ANIM/POLYTRAK2/POLYTRAK2_REBUILDEXPANDEDANIMATIONRESOURCE.CPP:31 proto [de,eu,eu-x,us,us-rev1], SOURCE/ENGINE/PPDECOM/PPDECOM_UNPACKRESOURCE.CPP:12 proto [de,eu,eu-x,us,us-rev1], SOURCE/ENGINE/SCENE/SCENE_LOADLEVELRESOURCES.CPP:773 proto [de,eu,eu-x,us,us-rev1] | struct CacheResourceBlockView*(int,void*,int) => SOURCE/ENGINE/GEOMETRY/GEOMETRY_EXPANDMODELCOMMANDS.CPP:31 proto [de,eu,eu-x,us,us-rev1], SOURCE/MENU/TRACK/TRACK_LOADTRACKSEQUENCE.CPP:21 proto [de,eu,eu-x,us,us-rev1], SOURCE/OVLGAME/SCENE/SCENE_BUILDCOLLISIONGEOMETRY.CPP:17 proto [de,eu,eu-x,us,us-rev1], SOURCE/OVLGAME/SCENE/SCENE_BUILDPLACEDACTORS.CPP:23 proto [de,eu,eu-x,us,us-rev1] | int**(int,void*,int) => SOURCE/ENGINE/TEXTLOAD/TEXTLOAD_LOADTEXTUREFRAME.CPP:11 proto [de,eu,eu-x,us,us-rev1]	9: SOURCE/ENGINE/ANIM/POLYTRAK2/POLYTRAK2_REBUILDEXPANDEDANIMATIONRESOURCE.CPP SOURCE/ENGINE/CACHE/CACHE/CACHE_ALLOCRESOURCEBLOCK.CPP SOURCE/ENGINE/GEOMETRY/GEOMETRY_EXPANDMODELCOMMANDS.CPP SOURCE/ENGINE/PPDECOM/PPDECOM_UNPACKRESOURCE.CPP SOURCE/ENGINE/SCENE/SCENE_LOADLEVELRESOURCES.CPP SOURCE/ENGINE/TEXTLOAD/TEXTLOAD_LOADTEXTUREFRAME.CPP SOURCE/MENU/TRACK/TRACK_LOADTRACKSEQUENCE.CPP SOURCE/OVLGAME/SCENE/SCENE_BUILDCOLLISIONGEOMETRY.CPP SOURCE/OVLGAME/SCENE/SCENE_BUILDPLACEDACTORS.CPP	open
g_Ovltab6EncodedWordsBlock6	2	char[] => SOURCE/DATA/OVERLAYS/SEVEN/RESOURCES/WEAPON_SLOT_6_BEHAVIOR_US_REV1.CPP:3 ext [us-rev1] | void*[] => SOURCE/DATA/WEAPON_STATE_TABLES/WEAPON_STATE_TABLES.CPP:18 vdef [us-rev1]	2: SOURCE/DATA/OVERLAYS/SEVEN/RESOURCES/WEAPON_SLOT_6_BEHAVIOR_US_REV1.CPP SOURCE/DATA/WEAPON_STATE_TABLES/WEAPON_STATE_TABLES.CPP	open
```
