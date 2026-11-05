# Amoment 胶片工作室

Amoment 是在浏览器里把照片调成胶片风格的工具。先挑相机和胶片，再导入照片调色，最后选择相纸留白或胶片边框并导出 JPG。照片处理在本地浏览器完成。

## 本地运行

安装 Node.js 后，在仓库根目录运行以下命令。

```sh
cd projects/amoment
node server.mjs
```

打开 [本地工作室](http://127.0.0.1:4173/)，点击封面右上角的拉绳进入。Windows 也可以双击 `start-preview.cmd`。前端、示例照片、模型与 Three.js 均随项目提供。

## 体验与实现

- 滚轮或方向键切换悬浮的 3D 相机和胶片，确认胶片时应用对应预设。[模型选择](dist/camera-room.js)
- 导入照片后可以调节曝光、对比度、饱和度、色温和颗粒，并拖动分隔线比较原图和调色结果。[照片编辑器](dist/studio.js)
- 点击 **Finish print** 后选择边框，预览和 JPG 导出共用同一套画幅与边距计算。[出片渲染](dist/print-frame.js)

胶片预设是风格化模拟。导入照片的处理长边上限为 2400 像素。可通过选择相纸边框、返回调色再进入出片页，检查照片、调色参数和边框设置是否保留。

示例照片的作者和来源见[照片清单](dist/photos.js)。Three.js 的授权文本随库提供。[第三方许可](dist/vendor/three-LICENSE.txt)
