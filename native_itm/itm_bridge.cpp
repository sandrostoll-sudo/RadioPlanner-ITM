/* RadioPlanner wrapper for NTIA ITM source, integrated 2026-11-02.
 * NTIA original source is unchanged; see upstream LICENSE.md.
 */
#include "itm_bridge.h"
#include "itm.h"
#include <vector>
#include <cmath>
#include <limits>
extern "C" int rp_itm_point_to_point(const double *heights_m, int count,
 double spacing_m, double tx_height_m, double rx_height_m,
 double frequency_mhz, double conductivity_s_m, double permittivity,
 double refractivity_n, double time_percent, int climate, int polarization,
 double location_percent, double confidence_percent, double reserved,
 double *loss_db) {
 (void)reserved;
 if (!loss_db || !heights_m || count < 3 || count > 200000 ||
     !std::isfinite(spacing_m) || spacing_m <= 0 ||
     !std::isfinite(frequency_mhz) || frequency_mhz < 20 || frequency_mhz > 20000 ||
     !std::isfinite(tx_height_m) || tx_height_m <= 0 ||
     !std::isfinite(rx_height_m) || rx_height_m <= 0) return 1001;
 std::vector<double> pfl(static_cast<size_t>(count)+2);
 pfl[0] = static_cast<double>(count-1);
 pfl[1] = spacing_m;
 for (int i=0;i<count;i++) {
   if (!std::isfinite(heights_m[i])) return 1002;
   pfl[static_cast<size_t>(i)+2] = heights_m[i];
 }
 double loss = std::numeric_limits<double>::quiet_NaN();
 long warnings = 0;
 // ITM point-to-point TLS mode, mdvar=1 (individual path).
 int status = ITM_P2P_TLS(tx_height_m, rx_height_m, pfl.data(), climate,
   refractivity_n, frequency_mhz, polarization, permittivity, conductivity_s_m,
   1, time_percent, location_percent, confidence_percent, &loss, &warnings);
 if (status != 0) return status;
 if (!std::isfinite(loss) || loss < 0) return 1003;
 *loss_db = loss;
 return 0;
}
