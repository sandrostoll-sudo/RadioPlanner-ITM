/* RadioPlanner ITM adapter ABI, version 1. All units SI except frequency MHz.
 * Caller provides count terrain heights at constant spacing (metres).
 * Implement with an independently validated ITM point-to-point engine.
 * Returns 0 on success, nonzero on error. loss_db is TOTAL basic path loss.
 * C calling convention (cdecl), 64-bit DLL on 64-bit Windows.
 */
#ifndef RADIOPLANNER_ITM_BRIDGE_H
#define RADIOPLANNER_ITM_BRIDGE_H
#ifdef __cplusplus
extern "C" {
#endif
#ifdef _WIN32
#define RP_EXPORT __declspec(dllexport)
#else
#define RP_EXPORT
#endif
RP_EXPORT int rp_itm_point_to_point(const double *heights_m, int count,
 double spacing_m, double tx_height_m, double rx_height_m,
 double frequency_mhz, double conductivity_s_m, double permittivity,
 double refractivity_n, double time_percent, int climate, int polarization,
 double location_percent, double confidence_percent, double reserved,
 double *loss_db);
#ifdef __cplusplus
}
#endif
#endif
