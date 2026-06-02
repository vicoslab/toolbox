const inferenceResults = document.getElementById("results");

async function makeRequest(form, action) {
    const formData = new FormData(form);
    try {
        const response = await fetch("/infer", {
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
            }
            #close {
                position: absolute;
                top: 0;
                right: 0;
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
        form.addEventListener("reset", _ => {
            this.boxes = [];
            shadow.querySelectorAll(".box").forEach(x => x.remove());
        });
        const draw = (x, y) => {
            box.style.width = Math.abs(x - this.startX) + "px";
            box.style.height = Math.abs(y - this.startY) + "px";
            box.style.left = Math.min(x, this.startX) + "px";
            box.style.top = Math.min(y, this.startY) + "px";
        }
        const handleStart = ({ clientX, clientY }) => {
            const rect = area.getBoundingClientRect();
            this.startX = clientX - rect.left;
            this.startY = clientY - rect.top;
            draw(this.startX, this.startY);
            box.style.display = "";
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

            box = document.createElement("div");
            box.style.display = "none";
            box.classList.add("box");
            area.append(box);

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
            .submit {
                border-radius: 0;
                padding: 0.5rem 1rem;
                border: 1px solid black;
                background-color: white;
                &:hover {
                    background-color: #fae4e4;
                }
                position: absolute;
                right: 0.5rem;
                bottom: 0.5rem;
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

        if (multiple && typeof this.onInference === "function") {
            const submit = document.createElement("input");
            submit.className = "submit";
            submit.type = "button";
            submit.value = "Submit";
            submit.addEventListener("click", _ => makeRequest(form, this.onInference));
            shadow.append(submit);
        }
    }
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
            }

            .options {
                flex-basis: 25%;
                padding: 0.5rem;

                >* {
                    margin: 0.25rem;
                }
            }

            .images {
                position: relative;
                height: fit-content;
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

customElements.define("infer-image", ImageInput);
customElements.define("infer-bbox", BBoxInput);
customElements.define("show-detections", ShowDetections);
