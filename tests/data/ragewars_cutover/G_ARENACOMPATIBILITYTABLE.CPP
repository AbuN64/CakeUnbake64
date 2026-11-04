#include "COMMON/FORMAT.H"
#include "COMMON/RESIDENT_EVENT_HANDLER.H"
#include "DATA_OVERLAY_LAYOUTS.H"
#include "TYPES.H"

#include "DATA/SCENARIO_SETUP/G_ARENACOMPATIBILITYTABLE.H"

/* 800E4A84.CPP */
#if defined(VERSION_US_REV1)
#if defined(VERSION_US_REV1)
OverlayTrialWidgetGroup g_arenaCompatibilityTable[64] = {
    { 422, 344, 368 }, { 422, 343, 360 }, { 344, 343, 366 }, { 343, 345, 346 }, { 343, 412, 373 }, { 345, 412, 411 }, { 412, 423, 413 }, { 412, 349, 380 },
    { 412, 347, 415 }, { 347, 349, 367 }, { 347, 354, 384 }, { 354, 350, 370 }, { 354, 351, 385 }, { 351, 419, 417 }, { 351, 352, 383 }, { 352, 419, 416 },
    { 419, 394, 421 }, { 419, 424, 420 }, { 353, 359, 438 }, { 353, 394, 395 }, { 353, 419, 418 }, { 355, 359, 407 }, { 355, 353, 389 }, { 355, 350, 391 },
    { 356, 359, 439 }, { 356, 355, 388 }, { 356, 349, 387 }, { 349, 355, 386 }, { 349, 350, 381 }, { 349, 354, 371 }, { 350, 353, 390 }, { 350, 419, 382 },
    { 350, 351, 369 }, { 424, 365, 425 }, { 424, 361, 378 }, { 361, 365, 374 }, { 361, 362, 375 }, { 365, 364, 426 }, { 365, 362, 427 }, { 364, 363, 392 },
    { 362, 364, 393 }, { 362, 363, 376 }, { 363, 379, 429 }, { 363, 431, 428 }, { 379, 431, 430 }, { 431, 397, 432 }, { 431, 434, 433 }, { 397, 396, 400 },
    { 397, 377, 398 }, { 434, 397, 435 }, { 434, 377, 436 }, { 377, 396, 399 }, { 377, 394, 372 }, { 396, 394, 437 }, { 394, 359, 441 }, { 394, 357, 442 },
    { 359, 357, 408 }, { 423, 358, 410 }, { 423, 357, 440 }, { 358, 357, 409 }, { 357, 401, 406 }, { 401, 402, 404 }, { 402, 403, 405 }, { 403, 414, 348 }
};
#endif
#endif

/* 800E4D84.CPP */
#if defined(VERSION_US_REV1)
extern char g_Results_CreateResultsOptionsScreenPackedAddress242[];
extern char g_Results_ShowResultsPlayerModelsPackedAddress242[];
extern char g_Results_AnimateResultsOptionsScreenPackedAddress242[];
extern char g_Results_RenderResultsOptionsPreviewPackedAddress242[];
extern char g_CTrialCatalogue_CloseTrialOptionsScreenPackedAddress242[];
extern char g_CTrialCatalogue_HandleTrialOptionReleasePackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborTwoPackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborThreePackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborOnePackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborZeroPackedAddress242[];
extern char g_CTrialCatalogue_ExitTrialCataloguePackedAddress242[];
extern char g_CTrialCatalogue_ConfirmTrialStagePackedAddress242[];
#if defined(VERSION_US_REV1)
ResidentEventHandlerEntry g_MenuEventHandlersPointerWords4[13] = {
    { 3591, 10, (ResidentEventHandler)(void *)&g_Results_ShowResultsPlayerModelsPackedAddress242 }, { 3592, 10, (ResidentEventHandler)(void *)&g_Results_AnimateResultsOptionsScreenPackedAddress242 }, { 10, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_HandleTrialOptionReleasePackedAddress242 }, { 3594, 10, (ResidentEventHandler)(void *)&g_Results_RenderResultsOptionsPreviewPackedAddress242 }, { 3590, 10, (ResidentEventHandler)(void *)&g_Results_CreateResultsOptionsScreenPackedAddress242 }, { 3587, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_CloseTrialOptionsScreenPackedAddress242 }, { 2, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_ExitTrialCataloguePackedAddress242 }, { 1, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_ConfirmTrialStagePackedAddress242 },
    { 5, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborTwoPackedAddress242 }, { 6, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborThreePackedAddress242 }, { 8, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborZeroPackedAddress242 }, { 7, 10, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborOnePackedAddress242 }, { 0, 0, (ResidentEventHandler)0 }
};
#endif
#endif

/* 800E4E20.CPP */
#if defined(VERSION_US_REV1)
extern char g_Results_CreateResultsOptionsScreenPackedAddress242[];
extern char g_Results_ShowResultsPlayerModelsPackedAddress242[];
extern char g_Results_AnimateResultsOptionsScreenPackedAddress242[];
extern char g_Results_RenderResultsOptionsPreviewPackedAddress242[];
extern char g_CTrialCatalogue_CloseTrialOptionsScreenPackedAddress242[];
extern char g_CTrialCatalogue_HandleTrialOptionReleasePackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborTwoPackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborThreePackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborOnePackedAddress242[];
extern char g_CTrialCatalogue_SelectNeighborZeroPackedAddress242[];
extern char g_CTrialCatalogue_ExitTrialCataloguePackedAddress242[];
extern char g_CTrialCatalogue_ConfirmTrialStagePackedAddress242[];
#if defined(VERSION_US_REV1)
ResidentEventHandlerEntry g_MenuEventHandlersPointerWords5[13] = {
    { 3591, 15, (ResidentEventHandler)(void *)&g_Results_ShowResultsPlayerModelsPackedAddress242 }, { 3592, 15, (ResidentEventHandler)(void *)&g_Results_AnimateResultsOptionsScreenPackedAddress242 }, { 10, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_HandleTrialOptionReleasePackedAddress242 }, { 3594, 15, (ResidentEventHandler)(void *)&g_Results_RenderResultsOptionsPreviewPackedAddress242 }, { 3590, 15, (ResidentEventHandler)(void *)&g_Results_CreateResultsOptionsScreenPackedAddress242 }, { 3587, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_CloseTrialOptionsScreenPackedAddress242 }, { 2, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_ExitTrialCataloguePackedAddress242 }, { 1, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_ConfirmTrialStagePackedAddress242 },
    { 5, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborTwoPackedAddress242 }, { 6, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborThreePackedAddress242 }, { 8, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborZeroPackedAddress242 }, { 7, 15, (ResidentEventHandler)(void *)&g_CTrialCatalogue_SelectNeighborOnePackedAddress242 }, { 0, 0, (ResidentEventHandler)0 }
};
#endif
#endif

/* 800E4EBC.CPP */
#if defined(VERSION_US_REV1)
#if defined(VERSION_US_REV1)
struct {
    u8 retainedFloatWord[4];
    ResidentEventHandlerEntry eventHandlers4[4];
} g_GArenacompatibilitytablePointerWords1 __attribute__((aligned(4))) = {
    { 0x3f, 0x19, 0x99, 0x9a },
    { { 3590, 14, (ResidentEventHandler)(void *)&g_Frontend_ClearLoadingHintsPackedAddress242 }, { 2, 14, (ResidentEventHandler)(void *)&g_Frontend_CloseMatchMenuPackedAddress242 }, { 1, 14, (ResidentEventHandler)(void *)&g_Frontend_HandleMatchMenuMessagePackedAddress242 }, { 0, 0, (ResidentEventHandler)0 } }
};
typedef char g_GArenacompatibilitytablePointerWords1SizeCheck[(sizeof(g_GArenacompatibilitytablePointerWords1) == 52) ? 1 : -1];
#endif
#endif
