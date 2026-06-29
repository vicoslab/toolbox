const inferenceResults = document.getElementById("results");

async function makeRequest(form, action, dispatch = true) {
    const formData = new FormData(form);
    try {
        const response = await fetch(window.endpoint, {
            method: "POST",
            body: formData,
        });
        if (dispatch) form.dispatchEvent(new Event("infer"));
        await response.json().then(response => {
            const elems = action(formData, response);
            if (elems !== null) {
                inferenceResults.replaceChildren(...[elems].flat());
            }
        });
    } catch (e) {
        console.error(e);
    }
}

class ImageInput extends HTMLElement {
    static formAssociated = true;

    constructor() {
        super();
        this.internals_ = this.attachInternals();
    }

    connectedCallback() {
        const shadow = this.attachShadow({ mode: "open" });

        const label = document.createElement("span");
        label.style.display = "grid";
        label.style.height = "100%";
        label.style.placeItems = "center";
        label.innerText = this.getAttribute("placeholder") || "Drag images here or click to open file selection menu";

        const input = document.createElement("input");
        input.setAttribute("type", "file");

        if (this.hasAttribute("multiple")) {
            input.setAttribute("multiple", "");
        }

        const wrapper = document.createElement("div");
        wrapper.style.position = "relative";
        wrapper.style.display = "none";
        wrapper.style.pointerEvents = "none";

        const image = document.createElement("img");
        const slot = document.createElement("slot");
        const close = document.createElement("button");
        close.style.display = "none";
        close.id = "close";
        close.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50" stroke-linecap="round" fill="none" stroke-width="4px" stroke="currentColor"><path d="M 15 35 L 35 15 M 15 15 L 35 35"/></svg>`;
        close.addEventListener("click", e => {
            e.preventDefault();
            this.internals_.form.reset();
            image.src = "";
            input.style.display = "";
            label.style.display = "";
            wrapper.style.display = "none";
            close.style.display = "none";
            inferenceResults.replaceChildren();
        });
        document.querySelector(".toolbar").append(close);

        const overlay = document.createElement("div");
        overlay.classList.add("overlay");
        overlay.append(slot);
        wrapper.append(image, overlay);

        const form = this.internals_.form || (() => { throw new Error("Inference input must be part of a form") })();
        form.addEventListener("infer", () => image.src = ""); // custom event
        const name = this.getAttribute("name") || (() => { throw new Error("Inference input must have a name") })();
        input.addEventListener("change", async (e) => {

            const data = new FormData();
            for (const file of input.files) {
                data.append(name, file, file.name);
            }
            this.internals_.setFormValue(data);

            if (typeof this.onInference === "function") {
                if (input.files.length <= 0) {
                    return;
                }
                wrapper.style.display = "";
                label.style.display = "none";
                makeRequest(form, this.onInference);
                input.value = null;
            } else if (input.files.length == 1) {
                const file = input.files.item(0);
                image.src = URL.createObjectURL(file);
                wrapper.style.display = "";
                close.style.display = "";
                input.style.display = "none";
                label.style.display = "none";
            }
        });

        const style = document.createElement("style");
        style.textContent = `
            input[type="file"] {
                opacity: 0;
                position: absolute;
                inset: 0;
                width: 100%;
            }
            #close {
                padding: 0;
                border: 0;
                width: 30px;
                height: 30px;
                pointer-events: auto;
            }
            img {
                object-fit: contain;
                max-width: 100%;
            }
            .overlay {
                position: "absolute";
                inset: 0;
                pointerEvents: "all";
            }
        `;

        shadow.append(label, input, wrapper, style);
    }
}

class BBoxInput extends HTMLElement {
    static formAssociated = true;

    constructor() {
        super();
        this.startX = null;
        this.startY = null;
        this.dragging = false;
        this.boxes = [];
        this.internals_ = this.attachInternals();
    }

    connectedCallback() {
        const shadow = this.attachShadow({ mode: "open" });
        const area = document.createElement("div");
        area.style.pointerEvents = "auto";
        area.classList.add("area");

        let box = document.createElement("div");
        box.style.display = "none";
        box.classList.add("box");
        area.append(box);

        const slot = document.createElement("slot");
        slot.style.display = "none";

        const multiple = this.hasAttribute("multiple");
        const buttons = document.createElement("div");
        buttons.style.pointerEvents = "auto";
        buttons.className = "buttons";

        const form = this.internals_.form || (() => { throw new Error("Inference input must be part of a form") })();
        const reset = () => {
            this.boxes = [];
            this.internals_.setFormValue("[]");
            shadow.querySelectorAll(".box").forEach(x => x.remove());
            area.style.display = "";
            slot.style.display = "none";
            buttons.style.display = "";
        };
        form.addEventListener("reset", reset);
        form.addEventListener("infer", () => {
            area.style.display = "none";
            buttons.style.display = "none";
            if (typeof this.onInference === "function") {
                slot.style.display = "";
            }
        });
        const draw = (x, y) => {
            box.style.width = Math.abs(x - this.startX) + "px";
            box.style.height = Math.abs(y - this.startY) + "px";
            box.style.left = Math.min(x, this.startX) + "px";
            box.style.top = Math.min(y, this.startY) + "px";
        }
        const handleStart = ({ clientX, clientY }) => {
            if (!multiple) reset();
            box = document.createElement("div");
            box.classList.add("box");
            area.append(box);

            const rect = area.getBoundingClientRect();
            this.startX = clientX - rect.left;
            this.startY = clientY - rect.top;
            draw(this.startX, this.startY);
            this.dragging = true;
        }
        const handleEnd = ({ clientX, clientY }) => {
            this.dragging = false;
            const { left, top, width, height } = area.getBoundingClientRect();
            const x = clientX - left;
            const y = clientY - top;
            this.boxes.push([
                Math.min(x, this.startX) / width,
                Math.min(y, this.startY) / height,
                Math.abs(x - this.startX) / width,
                Math.abs(y - this.startY) / height
            ]);
            this.internals_.setFormValue(JSON.stringify(this.boxes));
        }
        const handleMove = ({ clientX, clientY }) => {
            if (!this.dragging) return;
            const rect = area.getBoundingClientRect();
            draw(clientX - rect.left, clientY - rect.top);
        }

        area.addEventListener("touchstart", handleStart);
        area.addEventListener("touchmove", handleMove);
        area.addEventListener("touchend", handleEnd);
        area.addEventListener("mousedown", handleStart);
        area.addEventListener("mousemove", handleMove);
        area.addEventListener("mouseup", handleEnd);

        const style = document.createElement("style");
        style.textContent = `
            .buttons {
                position: absolute;
                right: 0.5rem;
                bottom: 0.5rem;
                display: flex;
                gap: 0.5rem;
            }
            input[type="button"] {
                border-radius: 0;
                padding: 0.5rem 1rem;
                border: 1px solid black;
                background-color: white;
                &:hover {
                    background-color: #fae4e4;
                }
            }
            .area {
                position: absolute;
                inset: 0;
            }
            .box {
                position: absolute;
                border: 1px solid orange;
                boxSizing: border-box;
            }
        `;
        const clear = document.createElement("input");
        clear.type = "button";
        clear.value = "Clear";
        clear.addEventListener("click", reset);
        buttons.append(clear);

        if (typeof this.onInference === "function") {
            const submit = document.createElement("input");
            submit.type = "button";
            submit.value = "Submit";
            submit.addEventListener("click", _ => makeRequest(form, this.onInference));
            buttons.append(submit);
        }

        shadow.append(area, slot, style, buttons);
    }
}

class VideoInput extends HTMLElement {
    static formAssociated = true;

    constructor() {
        super();
        this.internals_ = this.attachInternals();
    }

    connectedCallback() {
        const shadow = this.attachShadow({ mode: "open" });
        const form = this.internals_.form || (() => { throw new Error("Inference input must be part of a form") })();
        const name = this.getAttribute("name") || (() => { throw new Error("Inference input must have a name") })();

        const label = document.createElement("span");
        label.innerText = this.getAttribute("placeholder") || "Drag images here or click to open file selection menu";

        const input = document.createElement("input");
        input.setAttribute("type", "file");
        const close = document.createElement("button");
        close.style.display = "none";
        close.id = "close";
        close.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50" stroke-linecap="round" fill="none" stroke-width="4px" stroke="currentColor"><path d="M 15 35 L 35 15 M 15 15 L 35 35"/></svg>`;

        const fileOption = document.createElement("div");
        fileOption.className = "fileOption";
        fileOption.append(label, input);

        const cameraOption = document.createElement("div");
        cameraOption.className = "cameraOption";
        cameraOption.innerText = "Use camera";

        const inputOptions = document.createElement("div");
        inputOptions.append(fileOption, cameraOption);
        inputOptions.className = "inputOptions";

        const video = document.createElement("video");
        this.video = video;
        video.style.display = "none";
        video.controls = true;

        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext('2d');

        const slot = document.createElement("slot");
        slot.style.display = "none";

        const wrapper = document.createElement("div");
        wrapper.style.position = "relative";
        wrapper.append(canvas, video, slot);

        const style = document.createElement("style");
        style.innerText = `
            .fileOption {
                position: relative;
                display: grid;
                place-items: center;
                padding 0.5rem;
                
                input {
                    position: absolute;
                    inset: 0;
                    opacity: 0;
                }
            }
            .fileOption, .cameraOption {
                border: 1px solid black;
                width: 15rem;
                padding: 1rem;
                box-sizing: border-box;
                height: 100%;
            }
            .inputOptions {
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 1rem;
            }
            canvas {
                position: absolute;
                inset: 0;
                z-index: -1;
            }
            video {
                width: 100%;
            }
        `;
        close.addEventListener("click", e => {
            e.preventDefault();
            video.pause();
            video.src = "";
            video.style.display = "none";
            console.log("closed");
            this.internals_.form.reset();
            inputOptions.style.display = "";
            slot.style.display = "none";
            close.style.display = "none";
            inferenceResults.replaceChildren();
        });
        document.querySelector(".toolbar").append(close);

        // this should attempt to request and display images as fast as possible, without making excessive requests or lagging behind
        this.play = async (handler) => {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            let start = video.currentTime;
            await video.play();
            video.style.display = "none";
            let dispatch = true;
            while (!(video.ended || video.paused)) {
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                await new Promise(resolve => {
                    canvas.toBlob(blob => {
                        const data = new FormData();
                        data.append(name, blob);
                        this.internals_.setFormValue(data);
                        resolve();
                    }, 'image/webp');
                });
                await makeRequest(form, handler, dispatch = dispatch);
                dispatch = false;
            }
            return { start, end: video.currentTime, src: video.src };
        };

        shadow.append(inputOptions, wrapper, style);

        input.addEventListener("change", async () => {
            const file = input.files.item(0);
            if (file === null) {
                return;
            }
            video.src = URL.createObjectURL(file);

            slot.style.display = "";
            close.style.display = "";
            if (typeof this.onInference === "function") {
                this.play(this.onInference);
            } else {
                inputOptions.style.display = "none";
                const data = new FormData();
                data.append(name, file);
                this.internals_.setFormValue(data);
                video.style.display = "";
            }
        });

        cameraOption.addEventListener("click", async () => {
            console.log("click");
            const stream = await navigator.mediaDevices.getUserMedia({ video: true });
            slot.style.display = "";
            close.style.display = "";
            inputOptions.style.display = "none";
            video.srcObject = stream;
        });

        const setTimestamp = () => {
            const data = new FormData();
            for (const file of input.files) {
                data.append(name, file, file.name);
            }
            data.set(`${name}-timestamp`, video.currentTime);
            this.internals_.setFormValue(data);
        };
        video.addEventListener("seeked", setTimestamp);
        video.addEventListener("pause", setTimestamp);
    }
}

function settings(id) {
    const dialog = document.createElement("dialog");
    dialog.style.pointerEvents = "auto";
    dialog.id = id;

    const button = document.createElement("button");
    button.className = "settings";
    button.addEventListener("click", e => {
        e.preventDefault();
        dialog.showModal();
    });
    // FIXME: path generated by chatgpt, we can do better
    button.innerHTML = `<svg viewBox = "0 0 24 24" fill="currentColor"><path d="M19.14,12.94a7.43,7.43,0,0,0,.05-.94,7.43,7.43,0,0,0-.05-.94l2.03-1.58a.5.5,0,0,0,.12-.64l-1.92-3.32a.5.5,0,0,0-.6-.22L16.39,6.3a7.28,7.28,0,0,0-1.63-.94L14.4,2.81a.5.5,0,0,0-.49-.41H10.09a.5.5,0,0,0-.49.41L9.24,5.36a7.28,7.28,0,0,0-1.63.94L5.23,5.3a.5.5,0,0,0-.6.22L2.71,8.84a.5.5,0,0,0,.12.64l2.03,1.58a7.43,7.43,0,0,0-.05.94,7.43,7.43,0,0,0,.05.94L2.83,14.52a.5.5,0,0,0-.12.64l1.92,3.32a.5.5,0,0,0,.6.22l2.38-1a7.28,7.28,0,0,0,1.63.94l.36,2.55a.5.5,0,0,0,.49.41h3.82a.5.5,0,0,0,.49-.41l.36-2.55a7.28,7.28,0,0,0,1.63-.94l2.38,1a.5.5,0,0,0,.6-.22l1.92-3.32a.5.5,0,0,0-.12-.64ZM12,15.5A3.5,3.5,0,1,1,15.5,12,3.5,3.5,0,0,1,12,15.5Z"></path></svg>`;
    button.title = "Settings";

    return { button, dialog };
}

class ShowDetections extends HTMLElement {
    constructor() {
        super();
        this.defaultThreshold = 0.33;
        this.group = "detection-dialog";
    }

    connectedCallback() {
        console.log("connected callback");
        if (!this.reference) {
            throw new Error("Cannot create ShowDetections without reference");
        }
        if (!this.boxes && !this.masks) {
            throw new Error("Cannot create ShowDetections without bounding boxes or masks");
        }
        const showSettings = !!this.settings;

        this._key = `toolbox-ui-${this.group}`;
        if (!localStorage.getItem(this._key)) {
            localStorage.setItem(this._key, JSON.stringify({ hideBoxes: false, hideMasks: false, threshold: this.defaultThreshold }));
        }

        const shadow = this.attachShadow({ mode: "open" });
        const wrapper = document.createElement("div");
        wrapper.classList.add("wrapper");

        const images = document.createElement("div");
        images.classList.add("images");
        const reference = document.createElement("img");
        reference.src = this.reference;
        reference.classList.add("reference");
        const colors = Array.from(this.masks, (_, i) => (25 * i) % 360);
        const tagStyle = i => `--bg-accent-default: hsl(${colors[i]} 100% 50% / 0.3); --bg-accent-hover: hsl(${colors[i]} 100% 50% / 0.6); --bg-accent-active: hsl(${colors[i]} 100% 80%);`;
        const tagStyleHidden = i => `--bg-accent-default: hsl(${colors[i]} 100% 0% / 0.3); --bg-accent-hover: hsl(${colors[i]} 100% 0% / 0.4); --bg-accent-active: hsl(${colors[i]} 100% 0% / 0.5);`;

        let settingsDialog = document.getElementById(this._key);
        if (showSettings && !settingsDialog) {
            const { button, dialog } = settings(this._key);
            settingsDialog = dialog;
            this._removeSettings = () => { button.remove(); dialog.remove(); };

            const toggleMasks = document.createElement("input");
            toggleMasks.type = "checkbox";
            toggleMasks.id = "toggle-masks";
            const labelMasks = document.createElement("label");
            labelMasks.textContent = "Hide masks";
            labelMasks.htmlFor = "toggle-masks";

            const toggleBoxes = document.createElement("input");
            toggleBoxes.type = "checkbox";
            toggleBoxes.id = "toggle-boxes";

            const labelBoxes = document.createElement("label");
            labelBoxes.textContent = "Hide boxes";
            labelBoxes.htmlFor = "toggle-boxes";

            const toggleWrapper = document.createElement("div");
            toggleWrapper.style = "display: flex; justify-content: center; gap: 0.5rem; margin-bottom: 0.5rem;";
            toggleWrapper.append(toggleMasks, labelMasks, toggleBoxes, labelBoxes);

            const slider = document.createElement("input");
            slider.name = "threshold";
            slider.type = "range";
            slider.min = 0;
            slider.max = 1;
            const step = 0.01;
            slider.step = step;
            slider.value = this.defaultThreshold;

            const sliderLabel = document.createElement("label");
            sliderLabel.htmlFor = "threshold";
            sliderLabel.textContent = `Threshold: ${slider.value}`;

            const handler = () => dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: { hideBoxes: toggleBoxes.checked, hideMasks: toggleMasks.checked, threshold: slider.value } }));
            slider.addEventListener("input", () => {
                sliderLabel.textContent = `Threshold: ${slider.value}`;
                handler();
            });
            toggleBoxes.addEventListener("change", handler);
            toggleMasks.addEventListener("change", handler);

            button.addEventListener("click", () => {
                const saved = JSON.parse(localStorage.getItem(this._key));
                toggleBoxes.checked = saved.hideBoxes;
                toggleMasks.checked = saved.hideMasks;
                slider.value = saved.threshold;
                dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: saved }));
            });

            const cancel = document.createElement("button");
            cancel.innerText = "Cancel";
            cancel.addEventListener("click", e => {
                e.preventDefault();
                dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: JSON.parse(localStorage.getItem(this._key)) }));
                dialog.close();
            });

            const submit = document.createElement("button");
            submit.innerText = "Ok";
            submit.addEventListener("click", e => {
                e.preventDefault();
                const newval = { hideBoxes: toggleBoxes.checked, hideMasks: toggleMasks.checked, threshold: slider.value };
                localStorage.setItem(this._key, JSON.stringify(newval));
                dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: newval }));
                dialog.close();
            });
            const button_wrapper = document.createElement("div");
            button_wrapper.append(cancel, submit);
            button_wrapper.style.display = "flex";
            button_wrapper.style.justifyContent = "end";
            button_wrapper.style.gap = "0.5rem";
            button_wrapper.style.marginTop = "1rem";

            const settingsWrapper = document.createElement("div");
            settingsWrapper.style.display = "flex";
            settingsWrapper.style.flexDirection = "column";
            settingsWrapper.append(toggleWrapper, sliderLabel, slider, button_wrapper);
            dialog.append(settingsWrapper);

            document.querySelector(".toolbar").append(button, dialog);
        }

        let labels, masks, boxes;
        if (this.masks) {
            masks = this.masks
                .map((mask, i) => {
                    const maskWrapper = document.createElement("div");
                    maskWrapper.classList.add("maskWrapper");
                    maskWrapper.style = `--mask-color: hsl(${colors[i]} 100% 50%)`;
                    maskWrapper.classList.toggle("hidden", !!this.scores && this.scores[i] < this.defaultThreshold);

                    const img = document.createElement("img");
                    img.src = mask;
                    img.classList.add("mask");
                    maskWrapper.append(img);
                    return maskWrapper;
                });
            images.append(reference, ...masks);

            labels = masks.map((_, i) => {
                const tag = document.createElement("button");
                tag.innerText = `#${i}`;
                tag.style = !!this.scores && this.scores[i] < this.defaultThreshold ? "display: none;" : tagStyle(i);
                return tag;
            });
        }

        if (this.boxes) {
            if (!labels) {
                labels = this.boxes.map((_, i) => {
                    const tag = document.createElement("button");
                    tag.innerText = `#${i}`;
                    tag.style = !!this.scores && this.scores[i] < this.defaultThreshold ? "display: none;" : tagStyle(i);
                    return tag;
                });
            }
            boxes = this.boxes.map((_, i) => {
                const el = document.createElement("div");
                el.className = "box";
                el.classList.toggle("hidden", !!this.scores && this.scores[i] < this.defaultThreshold);
                return el;
            });
            reference.addEventListener("load", _ => {
                const { naturalWidth: width, naturalHeight: height } = reference;
                this.boxes.forEach(([x1, y1, x2, y2], i) => boxes[i].style = `position: absolute; top: ${y1 / height * 100}%; left: ${x1 / width * 100}%; width: ${(x2 - x1) / width * 100}%; height: ${(y2 - y1) / height * 100}%; border: 1px solid red;`);
            });
            images.append(...boxes);
        }

        const labelsWrapper = document.createElement("div");
        labelsWrapper.classList.add("labels");
        labelsWrapper.append(...labels);
        labelsWrapper.style.pointerEvents = "auto";
        wrapper.append(images, labelsWrapper);

        this.update = ({ reference: new_reference, masks: new_masks, boxes: new_boxes, scores }) => {
            this.scores = scores;
            reference.src = new_reference;
            new_masks?.forEach((m, i) => masks[i].querySelector(".mask").src = m);
            new_boxes?.forEach(([x1, y1, x2, y2], i) => boxes[i].style = `position: absolute; top: ${y1 / height * 100}%; left: ${x1 / width * 100}%; width: ${(x2 - x1) / width * 100}%; height: ${(y2 - y1) / height * 100}%; border: 1px solid red;`);
        };

        labels.map((tag, i) => {
            tag.addEventListener("click", () => {
                if (masks) tag.style = masks[i].classList.toggle("hidden") ? tagStyleHidden(i) : tagStyle(i);
                if (boxes) tag.style = boxes[i].classList.toggle("hidden") ? tagStyleHidden(i) : tagStyle(i);
            });
        });
        if (showSettings) {
            settingsDialog.addEventListener("settingsUpdate", ({ detail: { hideBoxes, hideMasks, threshold } }) => {
                images.classList.toggle("hideMasks", hideMasks);
                images.classList.toggle("hideBoxes", hideBoxes);
                this.scores?.forEach((s, i) => {
                    const hidden = s < threshold;
                    labels[i].style = hidden ? "display: none;" : tagStyle(i);
                    if (boxes) boxes[i].classList.toggle("hidden", hidden);
                    if (masks) masks[i].classList.toggle("hidden", hidden);
                });
            });
        }

        const style = document.createElement("style");
        style.textContent = `
            button {
                padding: 0.5rem 1rem;
                display: inline-block;
                background-color: var(--bg-accent-default);
                border: 1px solid black;
                &:hover {
                    background-color: var(--bg-accent-hover);
                }
                &:active {
                    background-color: var(--bg-accent-active);
                }
            }

            .labels {
                margin: auto;
                padding: 0.5rem;

                >* {
                    margin: 0.25rem;
                }
            }

            .images {
                overflow: hidden;
                position: relative;
                height: fit-content;
                width: fit-content;
                margin: auto;
            }

            .reference {
                object-fit: contain;
                max-width: 100%;
            }

            .masks {
                display: flex;
                flex-wrap: wrap;
                gap: 1px;
            }

            .hideMasks .maskWrapper, .hideBoxes .box {
                opacity: 0;
            }
            .maskWrapper {
                position: absolute;
                inset: 0;
                mix-blend-mode: lighten;
                opacity: 0.6;
                background-color: var(--mask-color, white);
            }

            .mask {
                max-width: 100%;
                mix-blend-mode: multiply;
                object-fit: contain;
            }

            .hidden {
                opacity: 0;
            }
        `;
        shadow.append(wrapper, style);
    }

    disconnectedCallback() {
        if (this._removeSettings) this._removeSettings();
    }
}

async function base64ToMat(base64) {
    return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => resolve(cv.imread(img));
        img.src = "data:image/webp;base64," + base64;
    });
}

class ShowActivation extends HTMLElement {
    constructor() {
        super();
        this.low = 0.5;
        this.high = 0.7;
        this.mode = "heatmap"; // overlay | heatmap
        this.group = "activation-dialog";
    }

    drawOverlay(low, high) {
        let gray = new cv.Mat();
        cv.cvtColor(this.map, gray, cv.COLOR_BGR2GRAY);

        let componentMask = new cv.Mat();
        cv.threshold(gray, componentMask, low * 255, 255, cv.THRESH_BINARY);

        let labels = new cv.Mat();
        let n = cv.connectedComponents(componentMask, labels, 8, cv.CV_32S);
        componentMask.delete();

        let highMask = new cv.Mat();
        cv.threshold(gray, highMask, high * 255, 255, cv.THRESH_BINARY);

        let contourCanvas = new cv.Mat.zeros(gray.rows, gray.cols, cv.CV_8UC4);
        for (let label = 1; label < n; label++) {

            let currentMask = new cv.Mat();
            let labelMat = new cv.Mat(labels.rows, labels.cols, cv.CV_32S, new cv.Scalar(label));

            cv.compare(labels, labelMat, currentMask, cv.CMP_EQ);
            labelMat.delete();

            let overlap = new cv.Mat();
            cv.bitwise_and(currentMask, highMask, overlap);

            if (cv.countNonZero(overlap) > 0) {

                let contours = new cv.MatVector();
                let hierarchy = new cv.Mat();

                cv.findContours(currentMask, contours, hierarchy, cv.RETR_TREE, cv.CHAIN_APPROX_NONE);

                if (contours.size() > 0) {
                    cv.drawContours(contourCanvas, contours, 0, new cv.Scalar(255, 0, 0, 255), 5);
                }

                contours.delete();
                hierarchy.delete();
            }

            overlap.delete();
            currentMask.delete();
        }
        cv.imshow(this.canvas, contourCanvas);

        contourCanvas.delete();
        highMask.delete();
        labels.delete();
        gray.delete();
    }

    drawHeatmap(low, high) {
        let gray = new cv.Mat();
        cv.cvtColor(this.map, gray, cv.COLOR_BGR2GRAY);

        let mapped = new cv.Mat();
        cv.applyColorMap(gray, mapped, cv.COLORMAP_JET);
        cv.cvtColor(mapped, mapped, cv.COLOR_BGR2RGBA);

        if (low > 0) {
            let channels = new cv.MatVector();
            let alpha = new cv.Mat();
            cv.threshold(gray, alpha, low * 255, 255, cv.THRESH_TOZERO);
            cv.split(mapped, channels);
            channels.set(3, alpha);
            alpha.delete();
            cv.merge(channels, mapped);

            const imageData = new ImageData(
                new Uint8ClampedArray(mapped.data),
                mapped.cols,
                mapped.rows
            );

            this.canvas.getContext("2d").putImageData(imageData, 0, 0);
        } else {
            cv.imshow(this.canvas, mapped);
        }

        mapped.delete();
        gray.delete();
    }

    draw({ low, high, mode }) {
        switch (mode) {
            case "overlay": return this.drawOverlay(low, high);
            case "heatmap": return this.drawHeatmap(low, high);
            default: throw new Error("Unknown mode for ShowActivation");
        }
    }

    async connectedCallback() {
        if (!this.map) {
            throw new Error("Cannot create ShowActivation without activation map");
        }
        this._key = `toolbox-ui-${this.group}`;
        let saved = localStorage.getItem(this._key);
        if (!saved) {
            saved = { low: this.low, high: this.high, mode: this.mode };
            localStorage.setItem(this._key, JSON.stringify(saved));
        } else {
            saved = JSON.parse(saved);
        }

        this.map = await base64ToMat(this.map);

        const shadow = this.attachShadow({ mode: "open" });

        let settingsDialog = document.getElementById(this._key);
        if (!settingsDialog) {
            const { button, dialog } = settings(this._key);
            settingsDialog = dialog;
            this._removeSettings = () => { button.remove(); dialog.remove(); };

            const wrapper = document.createElement("div");
            wrapper.style = "display: flex; flex-direction: column;";

            const low_id = `${this._key}-lower`;
            const low_label = document.createElement("label");
            low_label.htmlFor = low_id;
            low_label.textContent = "Lower threshold";

            const low = document.createElement("input");
            low.id = low_id;
            low.type = "range"
            low.min = 0;
            low.max = 1;
            low.step = 0.01;
            low.value = this.low;

            const high_id = `${this._key}-upper`;
            const high_label = document.createElement("label");
            high_label.htmlFor = high_id;
            high_label.textContent = "Upper threshold";

            const high = document.createElement("input");
            high.id = high_id;
            high.type = "range"
            high.min = 0;
            high.max = 1;
            high.step = 0.01;
            high.value = this.high;

            const period = 50; // time that needs to elapse between two events
            function debounced() {
                if (!debounced.lock) {
                    debounced.lock = setTimeout(() => {
                        const state = { low: low.value, high: high.value, mode: dialog.querySelector("input:checked").value };
                        dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: state }));
                        debounced.last = Date.now();
                        debounced.lock = null;
                    }, period - (Date.now() - debounced.last));
                }
            }
            debounced.last = Date.now();
            low.addEventListener("input", debounced);
            high.addEventListener("input", debounced);

            const modes = [].concat(...["overlay", "heatmap"].map(mode => {
                const id = `${this._key}-mode-${mode}`;
                const el = document.createElement("input");
                el.type = "radio";
                el.id = id;
                el.value = mode;
                el.name = "mode";
                if (this.mode === mode) el.checked = true;
                el.addEventListener("change", debounced);

                const label = document.createElement("label");
                label.htmlFor = id;
                label.textContent = mode[0].toUpperCase() + mode.substring(1);
                return [el, label];
            }));
            const modes_wrapper = document.createElement("div");
            modes_wrapper.append(...modes);

            button.addEventListener("click", () => {
                const saved = JSON.parse(localStorage.getItem(this._key));
                low.value = saved.low;
                high.value = saved.high;
                modes.forEach(x => {
                    if (x.tagName === "INPUT") {
                        x.checked = x.value === saved.mode;
                    }
                });
            });

            const cancel = document.createElement("button");
            cancel.innerText = "Cancel";
            cancel.addEventListener("click", e => {
                e.preventDefault();
                dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: JSON.parse(localStorage.getItem(this._key)) }));
                dialog.close();
            });

            const submit = document.createElement("button");
            submit.innerText = "Ok";
            submit.addEventListener("click", e => {
                e.preventDefault();
                const newval = { low: low.value, high: high.value, mode: dialog.querySelector("input:checked").value };
                localStorage.setItem(this._key, JSON.stringify(newval));
                dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: newval }));
                dialog.close();
            });
            const button_wrapper = document.createElement("div");
            button_wrapper.append(cancel, submit);
            button_wrapper.style = "display: flex; justify-content: end; gap: 0.5rem; margin-top: 1rem;";

            wrapper.append(low_label, low, high_label, high, modes_wrapper, button_wrapper);
            dialog.append(wrapper);
            document.querySelector(".toolbar").append(button, dialog);
        }

        this.canvas = document.createElement("canvas");
        this.canvas.width = this.map.cols;
        this.canvas.height = this.map.rows;
        this.draw(saved);

        settingsDialog.addEventListener("settingsUpdate", ({ detail }) => this.draw(detail));
        shadow.append(this.canvas);
    }

    disconnectedCallback() {
        this.map.delete();
        if (this._removeSettings) this._removeSettings();
    }
}

customElements.define("infer-image", ImageInput);
customElements.define("infer-bbox", BBoxInput);
customElements.define("infer-video", VideoInput);
customElements.define("show-detections", ShowDetections);
customElements.define("show-activation", ShowActivation);

window.addEventListener("load", () => {
    const style = document.createElement("style");
    style.innerText = `
        .toolbar {
            position: absolute;
            top: 0.5rem;
            right: 0.5rem;
            display: flex;
            gap: 0.5rem;
            flex-direction: row-reverse;

            > button, > input {
                width: 2.5rem;
                height: 2.5rem;
                padding: 0.25rem;
                pointer-events: auto;
            }
        }
    `;
    document.body.append(style);
});
