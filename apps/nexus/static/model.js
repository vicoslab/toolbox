const inferenceResults = document.getElementById("results");

async function makeRequest(form, action) {
    const formData = new FormData(form);
    try {
        const response = await fetch(window.endpoint, {
            method: "POST",
            body: formData,
        });
        await response.json().then(response => action(inferenceResults, formData, response));
        form.reset();
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
        label.innerText = this.getAttribute("placeholder") || "Drag images here or click to open file selection menu";

        const input = document.createElement("input");
        input.setAttribute("type", "file");

        if (this.hasAttribute("multiple")) {
            input.setAttribute("multiple", "");
        }

        const wrapper = document.createElement("div");
        wrapper.style.position = "relative";
        wrapper.style.display = "none";

        const image = document.createElement("img");

        const slot = document.createElement("slot");
        const overlay = document.createElement("div");
        overlay.classList.add("overlay");
        overlay.append(slot);

        const close = document.createElement("button");
        close.id = "close";
        close.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50" stroke-linecap="round" fill="none" stroke-width="4px" stroke="currentColor"><path d="M 15 35 L 35 15 M 15 15 L 35 35"/></svg>`;
        close.addEventListener("click", e => {
            e.preventDefault();
            this.internals_.form.reset();
            image.src = "";
            input.style.display = "";
            label.style.display = "";
            wrapper.style.display = "none";
        });

        overlay.append(slot, close);
        wrapper.append(image, overlay);

        const form = this.internals_.form || (() => { throw new Error("Inference input must be part of a form") })();
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
                makeRequest(form, this.onInference);
                input.value = null;
            } else if (input.files.length == 1) {
                const file = input.files.item(0);
                image.src = URL.createObjectURL(file);
                wrapper.style.display = "";
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
                margin: 0 10%;
            }
            #close {
                position: absolute;
                top: 0;
                right: 0;
                padding: 0;
                border: 0;
                width: 30px;
                height: 30px;
                background: #f55;
                color: white;
                border-radius: 50%;
                transform: translate(50%, -50%);
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
        area.classList.add("area");

        let box = document.createElement("div");
        box.style.display = "none";
        box.classList.add("box");
        area.append(box);

        const multiple = this.hasAttribute("multiple");

        const form = this.internals_.form || (() => { throw new Error("Inference input must be part of a form") })();
        const reset = () => {
            this.boxes = [];
            this.internals_.setFormValue("[]");
            shadow.querySelectorAll(".box").forEach(x => x.remove());
        }
        form.addEventListener("reset", reset);
        const draw = (x, y) => {
            box.style.width = Math.abs(x - this.startX) + "px";
            box.style.height = Math.abs(y - this.startY) + "px";
            box.style.left = Math.min(x, this.startX) + "px";
            box.style.top = Math.min(y, this.startY) + "px";
        }
        const handleStart = ({ clientX, clientY }) => {
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

            if (!multiple && typeof this.onInference === "function") {
                makeRequest(form, this.onInference);
            }
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

        shadow.append(area, style);

        if (multiple) {
            const buttons = document.createElement("div");
            buttons.className = "buttons";

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

            shadow.append(buttons);
        }
    }
}

function settings(id) {
    const dialog = document.createElement("dialog");
    dialog.id = id;

    const button = document.createElement("button");
    button.className = "settings";
    button.command = "show-modal";
    button.commandForElement = dialog;
    // FIXME: path generated by chatgpt, we can do better
    button.innerHTML = `<svg viewBox = "0 0 24 24" fill="currentColor"><path d="M19.14,12.94a7.43,7.43,0,0,0,.05-.94,7.43,7.43,0,0,0-.05-.94l2.03-1.58a.5.5,0,0,0,.12-.64l-1.92-3.32a.5.5,0,0,0-.6-.22L16.39,6.3a7.28,7.28,0,0,0-1.63-.94L14.4,2.81a.5.5,0,0,0-.49-.41H10.09a.5.5,0,0,0-.49.41L9.24,5.36a7.28,7.28,0,0,0-1.63.94L5.23,5.3a.5.5,0,0,0-.6.22L2.71,8.84a.5.5,0,0,0,.12.64l2.03,1.58a7.43,7.43,0,0,0-.05.94,7.43,7.43,0,0,0,.05.94L2.83,14.52a.5.5,0,0,0-.12.64l1.92,3.32a.5.5,0,0,0,.6.22l2.38-1a7.28,7.28,0,0,0,1.63.94l.36,2.55a.5.5,0,0,0,.49.41h3.82a.5.5,0,0,0,.49-.41l.36-2.55a7.28,7.28,0,0,0,1.63-.94l2.38,1a.5.5,0,0,0,.6-.22l1.92-3.32a.5.5,0,0,0-.12-.64ZM12,15.5A3.5,3.5,0,1,1,15.5,12,3.5,3.5,0,0,1,12,15.5Z"></path></svg>`;
    button.title = "Settings";
    button.style.position = "absolute";
    button.style.top = "0.5rem";
    button.style.right = "0.5rem";
    button.style.width = "2.5rem";
    button.style.height = "2.5rem";
    button.style.padding = "0.25rem";
    button.style.background = "#46b8fd";

    return { button, dialog };
}

class ShowDetections extends HTMLElement {
    constructor() {
        super();
        this.defaultThreshold = 0.33;
    }

    connectedCallback() {
        if (!this.reference) {
            throw new Error("Cannot create ShowDetections without reference");
        }
        if (!this.boxes && !this.masks) {
            throw new Error("Cannot create ShowDetections without bounding boxes or masks");
        }
        const shadow = this.attachShadow({ mode: "open" });
        const wrapper = document.createElement("div");
        wrapper.classList.add("wrapper");

        const images = document.createElement("div");
        images.classList.add("images");
        const reference = document.createElement("img");
        reference.src = URL.createObjectURL(this.reference);
        reference.classList.add("reference");
        const colors = Array.from(this.masks, () => Math.random() * 360);
        const tagStyle = i => `--bg-accent-default: hsl(${colors[i]} 100% 50% / 0.3); --bg-accent-hover: hsl(${colors[i]} 100% 50% / 0.6); --bg-accent-active: hsl(${colors[i]} 100% 80%);`;
        const tagStyleHidden = i => `--bg-accent-default: hsl(${colors[i]} 100% 0% / 0.3); --bg-accent-hover: hsl(${colors[i]} 100% 50% / 0.3); --bg-accent-active: hsl(${colors[i]} 100% 80%);`;

        const options = document.createElement("div");
        options.classList.add("options");

        if (this.masks && this.boxes) {
            const toggleMasks = document.createElement("input");
            toggleMasks.type = "checkbox";
            toggleMasks.id = "toggle-masks";
            toggleMasks.addEventListener("input", _ => images.classList.toggle("hideMasks", toggleMasks.checked));
            const labelMasks = document.createElement("label");
            labelMasks.textContent = "Hide masks";
            labelMasks.htmlFor = "toggle-masks";

            const toggleBoxes = document.createElement("input");
            toggleBoxes.type = "checkbox";
            toggleBoxes.id = "toggle-boxes";
            toggleBoxes.addEventListener("input", _ => images.classList.toggle("hideBoxes", toggleBoxes.checked));
            const labelBoxes = document.createElement("label");
            labelBoxes.textContent = "Hide boxes";
            labelBoxes.htmlFor = "toggle-boxes";

            const toggleWrapper = document.createElement("div");
            toggleWrapper.style = "display: flex; justify-content: center; gap: 0.5rem;";
            toggleWrapper.append(toggleMasks, labelMasks, toggleBoxes, labelBoxes);
            options.append(toggleWrapper);
        }
        let labels, masks, slider;
        if (this.masks) {
            masks = this.masks
                .map((mask, i) => {
                    const maskWrapper = document.createElement("div");
                    maskWrapper.classList.add("maskWrapper");
                    maskWrapper.style = `--mask-color: hsl(${colors[i]} 100% 50%)`;
                    maskWrapper.classList.toggle("hidden", !!this.scores && this.scores[i] < this.defaultThreshold);

                    const img = document.createElement("img");
                    img.src = `data: image / webp; base64, ${mask}`;
                    img.classList.add("mask");
                    maskWrapper.append(img);
                    return maskWrapper;
                });
            images.append(reference, ...masks);

            labels = masks.map((mask, i) => {
                const tag = document.createElement("button");
                tag.innerText = `#${i}`;
                tag.addEventListener("click", () => tag.style = mask.classList.toggle("hidden") ? tagStyleHidden(i) : tagStyle(i));
                tag.style = !!this.scores && this.scores[i] < this.defaultThreshold ? "display: none;" : tagStyle(i);
                return tag;
            });
        }

        if (this.scores) {
            const sliderWrapper = document.createElement("div");
            sliderWrapper.className = "slider-wrapper";
            const sliderLabel = document.createElement("label");
            sliderLabel.htmlFor = "threshold";
            slider = document.createElement("input");
            slider.name = "threshold";
            sliderWrapper.append(sliderLabel, slider);
            slider.type = "range";
            slider.min = 0;
            slider.max = 1;
            const step = 0.01;
            slider.step = step;
            slider.value = this.defaultThreshold;
            sliderLabel.textContent = `Threshold: ${slider.value}`;
            for (const score of this.scores) {
                if (score < slider.min) slider.min = score;
                else if (score > slider.max) slider.max = score + step;
            }
            slider.addEventListener("input", () => {
                sliderLabel.textContent = `Threshold: ${slider.value}`;
                this.scores.map((s, i) => labels[i].style = masks[i].classList.toggle("hidden", s < slider.value) ? "display: none;" : tagStyle(i))
            });
            options.appendChild(sliderWrapper);
        }
        options.append(...labels);
        wrapper.append(images, options);

        if (this.boxes) {
            reference.addEventListener("load", _ => {
                const { naturalWidth: width, naturalHeight: height } = reference;
                const boxes = this.boxes.map((box, i) => {
                    const [x1, y1, x2, y2] = box;
                    const el = document.createElement("div");
                    el.className = "box";
                    el.classList.toggle("hidden", !!this.scores && this.scores[i] < this.defaultThreshold);
                    el.style = `position: absolute; top: ${y1 / height * 100}%; left: ${x1 / width * 100}%; width: ${(x2 - x1) / width * 100}%; height: ${(y2 - y1) / height * 100}%; border: 1px solid red;`;
                    return el;
                });
                if (slider) {
                    slider.addEventListener("input", _ => this.scores.map((s, i) => boxes[i].classList.toggle("hidden", s < slider.value)));
                }
                if (labels) {
                    labels.map((tag, i) => tag.addEventListener("click", _ => boxes[i].classList.toggle("hidden")));
                }
                images.append(...boxes);
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
            .wrapper {
                display: flex;
                flex-wrap: wrap;
                justify-content: center;
            }

            .options {
                flex-basis: 25%;
                flex-grow: 1;
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

            .slider-wrapper {
                display: flex;
                flex-direction: column;
                align-items: center;
            }
        `;
        shadow.append(wrapper, style);
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
            cancel.commandForElement = dialog;
            cancel.command = "close";
            cancel.innerText = "Cancel";
            cancel.addEventListener("click", () => dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: JSON.parse(localStorage.getItem(this._key)) })));

            const submit = document.createElement("button");
            submit.innerText = "Ok";
            submit.commandForElement = dialog;
            submit.command = "close";
            submit.addEventListener("click", () => {
                const newval = { low: low.value, high: high.value, mode: dialog.querySelector("input:checked").value };
                localStorage.setItem(this._key, JSON.stringify(newval));
                dialog.dispatchEvent(new CustomEvent("settingsUpdate", { detail: newval }));
            });
            const button_wrapper = document.createElement("div");
            button_wrapper.append(cancel, submit);
            button_wrapper.style = "display: flex; justify-content: end; gap: 0.5rem; margin-top: 1rem;";

            wrapper.append(low_label, low, high_label, high, modes_wrapper, button_wrapper);
            dialog.append(wrapper);
            this.parentElement.append(button, dialog);
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
    }
}

customElements.define("infer-image", ImageInput);
customElements.define("infer-bbox", BBoxInput);
customElements.define("show-detections", ShowDetections);
customElements.define("show-activation", ShowActivation);
