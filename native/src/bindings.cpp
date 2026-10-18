#include "fpscoach/capture.hpp"
#include "fpscoach/movement_analyzer.hpp"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace fpscoach;

namespace {

py::dict summary_dict(const MovementSummary& value) {
    py::dict result;
    result["trial_count"] = value.trial_count;
    result["path_efficiency"] = value.path_efficiency;
    result["overshoot_score"] = value.overshoot_score;
    result["underreach_score"] = value.underreach_score;
    result["correction_score"] = value.correction_score;
    result["tremor_score"] = value.tremor_score;
    result["mean_endpoint_error_px"] = value.mean_endpoint_error_px;
    result["mean_duration_ms"] = value.mean_duration_ms;
    result["mean_speed_px_s"] = value.mean_speed_px_s;
    return result;
}

py::dict recommendation_dict(const CurveRecommendation& value) {
    py::dict result;
    result["points"] = value.points;
    result["sensitivity_multiplier"] = value.sensitivity_multiplier;
    result["confidence"] = value.confidence;
    result["title"] = value.title;
    result["reasons"] = value.reasons;
    return result;
}

py::dict stats_dict(const CaptureStats& value) {
    py::dict result;
    result["running"] = value.running;
    result["capture_fps"] = value.capture_fps;
    result["frames_captured"] = value.frames_captured;
    result["acquire_timeouts"] = value.acquire_timeouts;
    result["access_lost_count"] = value.access_lost_count;
    result["last_frame_age_ms"] = value.last_frame_age_ms;
    result["capture_width"] = value.capture_width;
    result["capture_height"] = value.capture_height;
    result["adapter_name"] = value.adapter_name;
    result["last_error"] = value.last_error;
    return result;
}

} // namespace

PYBIND11_MODULE(fpscoach_native, module) {
    module.doc() = "Native Desktop Duplication and movement-analysis core for FPS Coach";

    py::class_<DesktopDuplicationCapture>(module, "DesktopCapture")
        .def(py::init<>())
        .def(
            "start",
            [](DesktopDuplicationCapture& capture,
               const std::uint32_t adapter_index,
               const std::uint32_t output_index,
               const int x,
               const int y,
               const int width,
               const int height,
               const std::uint32_t target_fps) {
                CaptureConfig config{};
                config.adapter_index = adapter_index;
                config.output_index = output_index;
                config.region = {x, y, width, height};
                config.target_fps = target_fps;
                capture.start(config);
            },
            py::arg("adapter_index") = 0,
            py::arg("output_index") = 0,
            py::arg("x") = -1,
            py::arg("y") = -1,
            py::arg("width") = 640,
            py::arg("height") = 360,
            py::arg("target_fps") = 60,
            py::call_guard<py::gil_scoped_release>())
        .def("stop", &DesktopDuplicationCapture::stop, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("running", &DesktopDuplicationCapture::running)
        .def("stats", [](const DesktopDuplicationCapture& capture) {
            return stats_dict(capture.stats());
        })
        .def("latest_frame", [](const DesktopDuplicationCapture& capture) -> py::object {
            const auto frame = capture.latest_frame();
            if (!frame) {
                return py::none();
            }
            py::dict result;
            result["sequence"] = frame->sequence;
            result["timestamp_us"] = frame->timestamp_us;
            result["width"] = frame->width;
            result["height"] = frame->height;
            result["stride"] = frame->stride;
            result["pointer_visible"] = frame->pointer_visible;
            result["pointer_x"] = frame->pointer_x;
            result["pointer_y"] = frame->pointer_y;
            result["bgra"] = py::bytes(reinterpret_cast<const char*>(frame->pixels.data()),
                                        static_cast<py::ssize_t>(frame->pixels.size()));
            return std::move(result);
        })
        .def_static("enumerate_outputs", [] {
            py::list outputs;
            for (const auto& output : DesktopDuplicationCapture::enumerate_outputs()) {
                py::dict item;
                item["adapter_index"] = output.adapter_index;
                item["output_index"] = output.output_index;
                item["adapter_name"] = output.adapter_name;
                item["output_name"] = output.output_name;
                item["left"] = output.left;
                item["top"] = output.top;
                item["width"] = output.width;
                item["height"] = output.height;
                item["attached_to_desktop"] = output.attached_to_desktop;
                outputs.append(std::move(item));
            }
            return outputs;
        });

    py::class_<MovementAnalyzer>(module, "MovementAnalyzer")
        .def(py::init<>())
        .def("reset", &MovementAnalyzer::reset)
        .def("begin_trial",
             &MovementAnalyzer::begin_trial,
             py::arg("start_x"),
             py::arg("start_y"),
             py::arg("target_x"),
             py::arg("target_y"),
             py::arg("timestamp_seconds"))
        .def("add_sample",
             &MovementAnalyzer::add_sample,
             py::arg("x"),
             py::arg("y"),
             py::arg("timestamp_seconds"))
        .def("end_trial",
             &MovementAnalyzer::end_trial,
             py::arg("x"),
             py::arg("y"),
             py::arg("timestamp_seconds"))
        .def("summary", [](const MovementAnalyzer& analyzer) {
            return summary_dict(analyzer.summary());
        })
        .def("recommend", [](const MovementAnalyzer& analyzer) {
            return recommendation_dict(analyzer.recommend());
        })
        .def_property_readonly("trial_active", &MovementAnalyzer::trial_active);

    module.def("sample_cursor", []() -> py::object {
        POINT point{};
        if (!GetCursorPos(&point)) {
            return py::none();
        }
        return py::make_tuple(point.x, point.y);
    });
}
